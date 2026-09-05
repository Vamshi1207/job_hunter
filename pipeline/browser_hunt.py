"""Browse job boards and ATS pages with Camoufox. Never clicks Apply or Submit."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from pipeline.config import Config
from pipeline.jobs import (
    company_role_from_title,
    is_directory_or_salary_listing,
    is_job_posting_url,
    is_placeholder_company,
    parse_posting_meta,
)
from pipeline.search import html_to_text, hunt_location, hunt_locations, hunt_queries

log = logging.getLogger(__name__)

_on_listing: ContextVar = ContextVar("on_listing", default=None)
_on_stage: ContextVar = ContextVar("on_stage", default=None)
_should_stop: ContextVar = ContextVar("should_stop", default=None)


def _raise_if_cancelled(exc: BaseException) -> None:
    if isinstance(exc, asyncio.CancelledError):
        raise exc


def _notify_listing(item: dict | None) -> None:
    callback = _on_listing.get()
    if not callback or not item:
        return
    try:
        callback(item)
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.debug("on_listing failed", exc_info=True)


def _notify_stage(line: str) -> None:
    callback = _on_stage.get()
    if not callback or not (line or "").strip():
        return
    try:
        callback(line.strip())
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.debug("on_stage failed", exc_info=True)


def _stopped() -> bool:
    callback = _should_stop.get()
    try:
        return bool(callback and callback())
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def _pause_ms(ms: int) -> bool:
    """Sleep in short chunks so Stop can take effect. True if stop was requested."""
    remaining = max(0.0, (ms or 0) / 1000.0)
    while remaining > 0:
        if _stopped():
            return True
        step = min(0.4, remaining)
        await asyncio.sleep(step)
        remaining -= step
    return _stopped()


async def _wait_for_manual_auth(page, seconds: int, reason: str) -> bool:
    """Pause hunt until the login wall is gone, Stop, or timeout. True if signed in."""
    wait = max(0, int(seconds or 0))
    if wait <= 0:
        return not await _needs_login(page)
    if not await _needs_login(page):
        return True
    log.warning("%s Waiting up to %ss. Use the Camoufox panel on the desk.", reason, wait)
    _notify_stage(reason)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if _stopped():
            return False
        if not await _needs_login(page):
            _notify_stage("Signed in")
            return True
        await asyncio.sleep(1)
    still_blocked = await _needs_login(page)
    if still_blocked:
        log.warning("Still on a sign-in or verification page after %ss.", wait)
        _notify_stage("Sign-in wait ended")
        return False
    _notify_stage("Signed in")
    return True


DEFAULT_LINK_HINTS = (
    "/jobs/view/",
    "/viewjob",
    "jk=",
    "boards.greenhouse.io/",
    "job-boards.greenhouse.io/",
    "jobs.lever.co/",
    "jobs.ashbyhq.com/",
    "myworkdayjobs.com/",
    "smartrecruiters.com/",
    "jobs.workable.com/",
    "apply.workable.com/",
    "ats.rippling.com/",
    "wellfound.com/jobs/",
    "themuse.com/jobs/",
    "jobs.jobvite.com/",
    "icims.com/",
    "taleo.net/",
    "successfactors.com/",
    "bamboohr.com/",
    "recruitee.com/",
    "pinpointhq.com/",
    "breezy.hr/",
    "teamtailor.com/",
    "applytojob.com/",
    "eightfold.ai/",
    "otta.com/",
)

DEFAULT_TITLE_SELECTORS = (
    "h1",
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card__job-title",
    ".jobsearch-JobInfoHeader-title",
    "[data-test-job-title]",
    "h1.section-header",
)

DEFAULT_COMPANY_SELECTORS = (
    ".job-details-jobs-unified-top-card__company-name a",
    ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name",
    "a.topcard__org-name-link",
    "[data-company-name]",
    ".jobsearch-InlineCompanyRating-companyHeader a",
    "a[data-testid='employerName']",
    ".company-name",
)

DEFAULT_JD_SELECTORS = (
    ".jobs-description",
    "#job-details",
    ".jobs-box__html-content",
    "#jobDescriptionText",
    ".jobsearch-JobComponent-description",
    "[data-testid='job-description']",
    "article",
    "main",
)


def browser_enabled(cfg: Config) -> bool:
    return bool(cfg.get("hunt.browser.enabled", False))


def api_sources_enabled(cfg: Config) -> bool:
    raw = cfg.get("hunt.api_sources")
    if raw is None:
        return not browser_enabled(cfg)
    return bool(raw)


def _as_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(item).strip() for item in raw if str(item).strip()]


def fill_search_url(template: str, cfg: Config, query: str, extra: dict | None = None) -> str:
    location = hunt_location(cfg)
    city = (cfg.get("user.city") or "").strip()
    country = (cfg.get("user.country") or "").strip()
    values = {
        "query": quote_plus(query),
        "location": quote_plus(location),
        "city": quote_plus(city),
        "country": quote_plus(country),
        "query_raw": query,
        "location_raw": location,
        "dork": "",
        "ats": "",
    }
    if extra:
        values.update({key: extra[key] for key in extra if extra[key] is not None})
    return template.format(**values)


def build_google_dork(query: str, ats: str, location: str = "") -> str:
    """Role + ATS operator + location, e.g. "Software Engineer" site:boards.greenhouse.io Montreal, Canada."""
    operator = (ats or "").strip()
    if operator and not operator.lower().startswith("site:") and "." in operator and " " not in operator:
        operator = f"site:{operator.lstrip('/')}"
    parts = [f'"{query.strip()}"']
    if operator:
        parts.append(operator)
    if location.strip():
        parts.append(location.strip())
    return " ".join(parts)


def unwrap_result_url(url: str) -> str:
    """Follow Google/DDG redirect wrappers to the underlying ATS URL."""
    from urllib.parse import unquote

    parsed = urlparse(url.split("#")[0])
    host = parsed.netloc.lower()
    qs = parse_qs(parsed.query)
    if "google." in host and parsed.path.startswith("/url"):
        target = (qs.get("q") or qs.get("url") or [None])[0]
        if target and target.startswith("http"):
            return unquote(target)
    if "duckduckgo.com" in host:
        target = (qs.get("uddg") or [None])[0]
        if target:
            return unquote(target)
    return url


def canonicalize_job_url(url: str) -> str:
    parsed = urlparse(url.split("#")[0])
    host = parsed.netloc.lower()
    path = parsed.path
    if "linkedin.com" in host:
        match = re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", path) or re.search(r"/jobs/view/(\d+)", path)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match.group(1)}"
    if "indeed." in host:
        qs = parse_qs(parsed.query)
        jk = (qs.get("jk") or [None])[0]
        if jk:
            scheme = parsed.scheme or "https"
            return f"{scheme}://{parsed.netloc}/viewjob?jk={jk}"
    query = ""
    if "greenhouse.io" in host or "ashbyhq.com" in host or "lever.co" in host:
        query = parsed.query
    return urlunparse((parsed.scheme or "https", parsed.netloc, path.rstrip("/") or "/", "", query, ""))


def collect_job_links(html: str, base_url: str, contains: list[str] | None = None) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    hints = [item.lower() for item in (contains or list(DEFAULT_LINK_HINTS)) if item]
    skip_hosts = ("google.", "duckduckgo.com", "googleusercontent.com", "gstatic.com")
    found: list[str] = []
    seen: set[str] = set()

    def _add(href: str) -> None:
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http"):
            return
        href = unwrap_result_url(href)
        host = urlparse(href).netloc.lower()
        if any(skip in host for skip in skip_hosts):
            return
        low = href.lower()
        if not any(hint in low for hint in hints):
            return
        canon = canonicalize_job_url(href)
        if not is_job_posting_url(canon):
            return
        if canon in seen:
            return
        seen.add(canon)
        found.append(canon)

    for tag in soup.find_all("a", href=True):
        _add(urljoin(base_url, tag["href"]))
    host = urlparse(base_url).netloc.lower()
    if "indeed." in host:
        origin = f"{urlparse(base_url).scheme or 'https'}://{urlparse(base_url).netloc}"
        for tag in soup.find_all(attrs={"data-jk": True}):
            jk = (tag.get("data-jk") or "").strip()
            if jk:
                _add(f"{origin}/viewjob?jk={jk}")
    for match in re.finditer(r"/jobs/view/(?:[^/\"'?\s]*?-)?(\d{6,})", html or ""):
        _add(f"https://www.linkedin.com/jobs/view/{match.group(1)}")
    return found


def hunt_sources(cfg: Config) -> list[dict]:
    raw = cfg.get("hunt.sources")
    if not isinstance(raw, list):
        return []
    sources = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        sources.append(item)
    return sources


def _in_docker() -> bool:
    return os.environ.get("IN_DOCKER") == "1" or Path("/.dockerenv").exists()


def _profile_dir(cfg: Config) -> str:
    rel = cfg.get("hunt.browser.user_data_dir") or ".camoufox-profile"
    path = Path(rel).expanduser()
    if not path.is_absolute():
        path = cfg.root / path
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _source_name(source: dict) -> str:
    return str(source.get("id") or source.get("name") or "board")


def source_label(source: dict) -> str:
    raw = _source_name(source)
    key = raw.lower().replace(" ", "_")
    labels = {
        "linkedin": "LinkedIn",
        "indeed": "Indeed",
        "google": "Google",
        "google_ats": "Google",
        "ats": "company boards",
        "boards": "company boards",
        "saved": "saved jobs",
        "desk": "pasted URLs",
        "greenhouse": "Greenhouse",
        "lever": "Lever",
    }
    return labels.get(key, raw.replace("_", " "))


def saved_jobs_enabled(cfg: Config) -> bool:
    raw = cfg.get("hunt.saved_jobs.enabled")
    if raw is None:
        return True
    return bool(raw)


def saved_job_urls(cfg: Config) -> list[str]:
    configured = _as_list(cfg.get("hunt.saved_jobs.urls"))
    if configured:
        return configured
    urls = ["https://www.linkedin.com/my-items/saved-jobs/"]
    country = (cfg.get("user.country") or "").strip().lower()
    if country in {"canada", "ca"}:
        urls.extend(
            [
                "https://ca.indeed.com/saved",
                "https://ca.indeed.com/savedjobs",
            ]
        )
    else:
        urls.extend(
            [
                "https://www.indeed.com/saved",
                "https://www.indeed.com/savedjobs",
            ]
        )
    return urls


async def browse_jobs(cfg: Config, on_listing=None, on_stage=None, should_stop=None) -> list[dict]:
    """Open Camoufox, walk saved jobs then configured search/ATS URLs, return listings with JD text."""
    sources = hunt_sources(cfg)
    if not sources and not saved_jobs_enabled(cfg):
        log.warning("hunt.browser is on but hunt.sources is empty.")
        return []
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        log.error("Camoufox is not installed. Run: python3 -m pip install camoufox && python3 -m camoufox fetch")
        return []

    delay_ms = int(cfg.get("hunt.browser.page_delay_ms", 1500) or 1500)
    login_wait = int(cfg.get("hunt.browser.login_wait_seconds", 120) or 0)
    max_per = int(cfg.get("hunt.browser.max_per_source", 40) or 40)
    max_per = max(1, min(80, max_per))
    launch = _camoufox_launch(cfg)
    log.info(
        "Opening Camoufox (headless=%s, persistent=%s). Sign in if a board asks. Never clicking Apply.",
        launch.get("headless"),
        bool(launch.get("persistent_context")),
    )
    listing_token = _on_listing.set(on_listing)
    stage_token = _on_stage.set(on_stage)
    stop_token = _should_stop.set(should_stop)
    listings: list[dict] = []
    try:
        if _stopped():
            return []
        _notify_stage("Opening job boards")
        async with AsyncCamoufox(**launch) as session:
            page = await session.new_page()
            if saved_jobs_enabled(cfg) and not _stopped():
                _notify_stage("Reading saved jobs")
                saved = await _collect_saved_jobs(page, cfg, delay_ms, login_wait, max_per)
                log.info("Saved jobs: %s posting(s)", len(saved))
                listings.extend(saved)
            for source in sources:
                if _stopped():
                    break
                try:
                    _notify_stage(f"Searching {source_label(source)}")
                    found = await _crawl_source(page, cfg, source, delay_ms, login_wait, max_per)
                    log.info("Camoufox %s: %s posting(s)", _source_name(source), len(found))
                    listings.extend(found)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _raise_if_cancelled(exc)
                    log.warning("Camoufox source %s failed: %s", _source_name(source), exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.error("Camoufox failed to start: %s. Run python3 -m camoufox fetch", exc)
        return []
    finally:
        _on_listing.reset(listing_token)
        _on_stage.reset(stage_token)
        _should_stop.reset(stop_token)
    return listings


def _camoufox_launch(cfg: Config) -> dict[str, Any]:
    headless = cfg.get("hunt.browser.headless", False)
    if _in_docker() and headless is False:
        # Headed on compose Xvfb (DISPLAY=:99) so the desk noVNC panel can show
        # Firefox. Camoufox headless="virtual" starts a private Xvfb the desk
        # cannot see.
        headless = False
    launch: dict[str, Any] = {
        "headless": headless,
        "humanize": bool(cfg.get("hunt.browser.humanize", True)),
        "env": {
            **os.environ,
            "MOZ_DISABLE_CONTENT_SANDBOX": "1",
            "MOZ_DBUS_REMOTE": "1",
        },
        "firefox_user_prefs": {
            "security.sandbox.content.level": 0,
        },
    }
    display = os.environ.get("DISPLAY")
    if display:
        launch["env"]["DISPLAY"] = display
    os_name = cfg.get("hunt.browser.os") or None
    if os_name:
        launch["os"] = os_name
    if cfg.get("hunt.browser.persistent", True):
        launch["persistent_context"] = True
        launch["user_data_dir"] = _profile_dir(cfg)
    return launch


async def hydrate_job_urls(cfg: Config, urls: list[str], on_listing=None, on_stage=None, should_stop=None) -> list[dict]:
    """Read company, role, and JD for pasted URLs. LinkedIn/Indeed use Camoufox."""
    from pipeline.jobs import fetch_posting

    listing_token = _on_listing.set(on_listing)
    stage_token = _on_stage.set(on_stage)
    stop_token = _should_stop.set(should_stop)
    listings: list[dict] = []
    pending: list[str] = []
    seen: set[str] = set()
    try:
        _notify_stage(f"Reading {len(urls)} posting(s)")
        for url in urls:
            if _stopped():
                return listings
            url = (url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            if not is_job_posting_url(url):
                log.info("Skip non-job page %s", url)
                continue
            posting = fetch_posting(url)
            if (
                posting
                and (posting.get("jd") or "").strip()
                and (posting.get("role") or "").strip()
                and not is_placeholder_company(posting.get("company") or "")
                and not is_directory_or_salary_listing(posting)
            ):
                listings.append(posting)
                _notify_listing(posting)
            else:
                pending.append(url)
        if pending and not _stopped():
            _notify_stage("Opening job boards to read postings")
            listings.extend(await _hydrate_with_camoufox(cfg, pending))
        return listings
    finally:
        _on_listing.reset(listing_token)
        _on_stage.reset(stage_token)
        _should_stop.reset(stop_token)


async def _hydrate_with_camoufox(cfg: Config, urls: list[str]) -> list[dict]:
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        log.error("Camoufox is not installed. Run: python3 -m pip install camoufox && python3 -m camoufox fetch")
        return []
    delay_ms = int(cfg.get("hunt.browser.page_delay_ms", 1500) or 1500)
    launch = _camoufox_launch(cfg)
    log.info("Opening Camoufox to read %s pasted job URL(s). Never clicking Apply.", len(urls))
    found: list[dict] = []
    source = {"id": "desk"}
    try:
        async with AsyncCamoufox(**launch) as session:
            page = await session.new_page()
            for url in urls:
                if _stopped():
                    break
                item = await _extract_posting(page, cfg, source, url, delay_ms)
                if item:
                    item["source"] = item.get("source") or "desk"
                    found.append(item)
                    _notify_listing(item)
                else:
                    log.warning("Could not read posting %s", url)
    except Exception as exc:
        log.error("Camoufox failed to start: %s. Run python3 -m camoufox fetch", exc)
        return []
    return found


async def _crawl_source(page, cfg: Config, source: dict, delay_ms: int, login_wait: int, max_per: int) -> list[dict]:
    kind = (source.get("kind") or "search").lower()
    if kind == "boards":
        urls = _as_list(source.get("boards")) or _as_list(cfg.get("hunt.ats_boards"))
        if not urls:
            log.info("No ATS board URLs in hunt.ats_boards — skipping ats source.")
            return []
        listings = []
        for board_url in urls[:40]:
            if _stopped():
                break
            listings.extend(
                await _collect_from_url(page, cfg, source, board_url, delay_ms, login_wait, max_per)
            )
        return listings

    if kind in {"google_ats", "google"}:
        return await _crawl_google_ats(page, cfg, source, delay_ms, login_wait, max_per)

    queries = hunt_queries(cfg)
    template = source.get("url") or source.get("url_template") or ""
    if not template:
        log.warning("Source %s has no url template", _source_name(source))
        return []
    listings = []
    seen = set()
    locations = hunt_locations(cfg)
    # Balance max_per across locations so the first location doesn't starve the others
    per_location = max(5, min(max_per, (max_per // max(1, len(locations))) + 2)) if locations else max_per
    for location in locations:
        if _stopped():
            break
        is_remote = "remote" in location.lower()
        clean_loc = re.sub(r"\bremote\b", "", location, flags=re.I).strip() or "Canada"
        for query in queries:
            if _stopped():
                break
            search_url = fill_search_url(
                template,
                cfg,
                query,
                extra={"location": quote_plus(clean_loc), "location_raw": clean_loc},
            )
            # Inject remote filter parameters when searching for remote roles
            if is_remote:
                if "linkedin.com" in search_url:
                    # In LinkedIn, f_WT=2 specifies Remote work
                    if "f_WT=2" not in search_url:
                        search_url += "&f_WT=2"
                elif "indeed.com" in search_url:
                    search_url = re.sub(r"l=[^&]+", "l=Remote", search_url)

            for item in await _collect_from_url(page, cfg, source, search_url, delay_ms, login_wait, per_location):
                key = (item.get("url") or "").lower()
                if key in seen:
                    continue
                seen.add(key)
                listings.append(item)
    return listings


async def _crawl_google_ats(page, cfg: Config, source: dict, delay_ms: int, login_wait: int, max_per: int) -> list[dict]:
    """Search Google (or another engine) for role + ATS operator, then open the job URLs."""
    template = source.get("url") or "https://www.google.com/search?q={dork}&hl=en&num=10"
    ats_ops = _as_list(source.get("ats")) or _as_list(cfg.get("hunt.google_ats"))
    if not ats_ops:
        log.warning("google_ats source has no hunt.sources[].ats / hunt.google_ats operators.")
        return []
    max_queries = max(1, min(8, int(source.get("max_queries") or 1)))
    group_size = max(1, min(8, int(source.get("group_size") or 1)))
    queries = hunt_queries(cfg)[:max_queries]
    locations = hunt_locations(cfg)
    groups = [ats_ops[i : i + group_size] for i in range(0, len(ats_ops), group_size)]
    listings = []
    seen = set()
    per_page = max(1, min(max_per, int(source.get("max_results") or 4)))
    for query in queries:
        if _stopped():
            break
        for location in locations:
            if _stopped():
                break
            for group in groups:
                if _stopped():
                    break
                if group_size == 1:
                    dork = build_google_dork(query, group[0], location)
                    extra = {"ats": quote_plus(group[0]), "dork": quote_plus(dork)}
                else:
                    joined = " OR ".join(group)
                    dork = build_google_dork(query, f"({joined})", location)
                    extra = {"ats": quote_plus(joined), "dork": quote_plus(dork)}
                search_url = fill_search_url(template, cfg, query, extra)
                log.info("Google ATS dork: %s", dork)
                for item in await _collect_from_url(page, cfg, source, search_url, delay_ms, login_wait, per_page):
                    key = (item.get("url") or "").lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    listings.append(item)
    return listings


SAVED_LIST_MAX_PAGES = 25

# Click LinkedIn/Indeed listing pagination. Never matches Apply/Submit.
_CLICK_NEXT_PAGE_JS = """() => {
  const labelOf = (el) =>
    `${el.getAttribute("aria-label") || ""} ${el.textContent || ""}`.replace(/\\s+/g, " ").trim();
  const disabled = (el) =>
    el.disabled ||
    el.getAttribute("aria-disabled") === "true" ||
    el.classList.contains("disabled") ||
    el.classList.contains("artdeco-button--disabled");
  const applyLike = (text) => /\\b(apply|submit)\\b/i.test(text);
  const nodes = [...document.querySelectorAll("button, a, [role='button']")];
  const next = nodes.find((el) => {
    if (disabled(el)) return false;
    const text = labelOf(el);
    if (applyLike(text)) return false;
    const testid = (el.getAttribute("data-testid") || "").toLowerCase();
    if (/^next\\b/i.test(text) || /next page/i.test(text)) return true;
    if (testid.includes("pagination-page-next")) return true;
    if (el.classList.contains("artdeco-pagination__button--next")) return true;
    return false;
  });
  if (next) {
    next.click();
    return "next";
  }
  const current = nodes.find(
    (el) => el.getAttribute("aria-current") === "true" || el.getAttribute("aria-current") === "page"
  );
  const n = current ? parseInt((current.textContent || "").trim(), 10) : NaN;
  if (n >= 1) {
    const want = String(n + 1);
    const pageBtn = nodes.find((el) => {
      if (disabled(el) || applyLike(labelOf(el))) return false;
      const aria = (el.getAttribute("aria-label") || "").trim();
      if (/^page\\s+/i.test(aria) && aria.replace(/[^0-9]/g, "") === want) return true;
      return (el.textContent || "").trim() === want && /pagination/i.test(el.className || el.parentElement?.className || "");
    });
    if (pageBtn) {
      pageBtn.click();
      return "page";
    }
  }
  return "";
}"""

_NEXT_PAGE_SELECTORS = (
    "button.artdeco-pagination__button--next:not([disabled])",
    'button[aria-label="Next"]:not([disabled])',
    'button[aria-label="Next Page"]:not([disabled])',
    'a[aria-label="Next"]',
    'a[aria-label="Next Page"]',
    'a[data-testid="pagination-page-next"]',
    'button[data-testid="pagination-page-next"]',
)


def listing_next_is_enabled(html: str) -> bool:
    """True when a listings page has a usable Next / page-N control (not Apply)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for el in soup.find_all(["button", "a"]):
        aria = el.get("aria-label") or ""
        if isinstance(aria, (list, tuple)):
            aria = " ".join(str(part) for part in aria)
        text = f"{aria} {el.get_text(' ', strip=True)}".strip()
        classes = " ".join(el.get("class") or [])
        testid = str(el.get("data-testid") or "")
        if el.has_attr("disabled") or str(el.get("aria-disabled") or "").lower() == "true":
            continue
        if "disabled" in classes:
            continue
        if re.search(r"\b(apply|submit)\b", text, re.I):
            continue
        if re.match(r"^next\b", text, re.I) or re.search(r"next page", text, re.I):
            return True
        if "pagination-page-next" in testid.lower() or "artdeco-pagination__button--next" in classes:
            return True
    return False


def _mark_saved_listing(item: dict, link: str) -> dict:
    item["saved"] = True
    host = urlparse(item.get("url") or link).netloc.lower()
    board = "linkedin" if "linkedin.com" in host else "indeed" if "indeed." in host else "board"
    item["source"] = f"saved:{board}"
    return item


async def _open_listings_url(page, cfg: Config, url: str, delay_ms: int, login_wait: int) -> bool:
    if _stopped():
        return False
    log.info("Camoufox open %s", url)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if await _pause_ms(max(400, delay_ms)):
        return False
    if await _needs_login(page):
        signed_in = await _try_configured_login(page, cfg, delay_ms)
        if not signed_in or await _needs_login(page):
            await _wait_for_manual_auth(
                page,
                login_wait,
                "Sign in or extra verification — use the Camoufox panel",
            )
        if _stopped():
            return False
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if await _pause_ms(max(400, delay_ms)):
            return False
    return not _stopped()


async def _click_next_listings_page(page, delay_ms: int) -> bool:
    """Click Next (or the next page number). False if there is no further page."""
    before_url = page.url or ""
    before_html = ""
    try:
        before_html = await page.content()
    except Exception as exc:
        _raise_if_cancelled(exc)
    clicked = ""
    try:
        clicked = str(await page.evaluate(_CLICK_NEXT_PAGE_JS) or "")
    except Exception as exc:
        _raise_if_cancelled(exc)
    if not clicked:
        if await _click_first(page, list(_NEXT_PAGE_SELECTORS)):
            clicked = "selector"
    if not clicked:
        return False
    if await _pause_ms(max(800, delay_ms)):
        return False
    after_url = page.url or ""
    if after_url != before_url:
        return True
    try:
        after_html = await page.content()
    except Exception as exc:
        _raise_if_cancelled(exc)
        return True
    return after_html != before_html


async def _clean_and_unsave_linkedin_saved_jobs(
    page,
    cfg: Config,
    delay_ms: int,
) -> set[str]:
    """Inspect LinkedIn saved jobs on the active page.
    Unsave any job that is already applied or deleted/closed.
    Returns set of job identifiers (IDs and URLs) that were skipped/unsaved.
    """
    from pipeline.jobs import (
        applied_job_urls,
        applied_linkedin_ids,
        deleted_job_urls,
        deleted_linkedin_ids,
        extract_linkedin_job_id,
        record_deleted_job,
        set_job_applied,
    )

    known_applied_urls = applied_job_urls(cfg)
    known_applied_ids = applied_linkedin_ids(cfg)
    known_deleted_urls = deleted_job_urls(cfg)
    known_deleted_ids = deleted_linkedin_ids(cfg)

    scan_js = """
    () => {
      const cards = Array.from(document.querySelectorAll(
        'li.reusable-search__result-container, div.entity-result, li[data-chameleon-result-urn], .job-card-container, .my-items-job-card'
      ));
      return cards.map((card, idx) => {
        const link = card.querySelector('a[href*="/jobs/view/"]');
        const href = link ? link.href : '';
        const text = (card.innerText || '').trim();
        const directBtn = card.querySelector('button[aria-label*="unsave" i], button.jobs-save-button');
        const moreBtn = card.querySelector('button[aria-label*="more action" i], button[aria-label*="more" i], button[aria-label*="option" i], button.artdeco-dropdown__trigger, .entity-result__actions button');
        return {
          index: idx,
          href: href,
          text: text,
          hasDirect: Boolean(directBtn),
          hasMore: Boolean(moreBtn),
        };
      });
    }
    """
    try:
        cards_info = await page.evaluate(scan_js)
    except Exception as exc:
        _raise_if_cancelled(exc)
        return set()

    if not isinstance(cards_info, list):
        return set()

    unsaved_keys: set[str] = set()

    for item in cards_info:
        if _stopped():
            break
        href = str(item.get("href") or "").strip()
        text = str(item.get("text") or "")
        jid = extract_linkedin_job_id(href)
        clean_url = href.split("?")[0].split("#")[0].lower()

        card_has_applied = bool(re.search(r"\b(applied|applied\s+\d+|you\s+applied)\b", text, re.I))
        card_has_closed = bool(re.search(r"\b(no longer accepting applications|closed|job closed|deleted)\b", text, re.I))

        is_applied = (jid and jid in known_applied_ids) or (clean_url and clean_url in known_applied_urls) or card_has_applied
        is_deleted = (jid and jid in known_deleted_ids) or (clean_url and clean_url in known_deleted_urls) or card_has_closed

        if not is_applied and not is_deleted:
            continue

        reason = "applied" if is_applied else "deleted"
        if jid:
            unsaved_keys.add(jid)
        if clean_url:
            unsaved_keys.add(clean_url)
        if href:
            unsaved_keys.add(href.lower())

        idx = item.get("index", 0)
        click_card_js = """
        (arg) => {
          const { index, href } = arg;
          const cards = Array.from(document.querySelectorAll(
            'li.reusable-search__result-container, div.entity-result, li[data-chameleon-result-urn], .job-card-container, .my-items-job-card'
          ));
          let card = cards[index];
          if (!card && href) {
            card = cards.find(c => {
              const a = c.querySelector('a[href*="/jobs/view/"]');
              return a && a.href && a.href.includes(href);
            });
          }
          if (!card) return { success: false, reason: "card_not_found" };

          const directBtn = card.querySelector('button[aria-label*="unsave" i], button.jobs-save-button');
          if (directBtn) {
            directBtn.click();
            return { success: true, method: "direct" };
          }

          const moreBtn = card.querySelector('button[aria-label*="more action" i], button[aria-label*="more" i], button[aria-label*="option" i], button.artdeco-dropdown__trigger, .entity-result__actions button');
          if (moreBtn) {
            moreBtn.click();
            return { success: true, method: "dropdown_opened" };
          }
          return { success: false, reason: "no_button" };
        }
        """
        try:
            res = await page.evaluate(click_card_js, {"index": idx, "href": href})
            if isinstance(res, dict) and res.get("method") == "dropdown_opened":
                await _pause_ms(250)
                click_dropdown_js = """
                () => {
                  const dropdownItems = Array.from(document.querySelectorAll(
                    '.artdeco-dropdown__content button, .artdeco-dropdown__item, [role="menuitem"], .artdeco-dropdown__content li'
                  ));
                  const unsaveItem = dropdownItems.find(el => {
                    const txt = (el.innerText || '').toLowerCase();
                    const lbl = (el.getAttribute('aria-label') || '').toLowerCase();
                    return txt.includes('unsave') || txt.includes('remove') || lbl.includes('unsave');
                  });
                  if (unsaveItem) {
                    unsaveItem.click();
                    return true;
                  }
                  return false;
                }
                """
                await page.evaluate(click_dropdown_js)
            log.info("Camoufox unsaved LinkedIn %s job from saved list: %s (id: %s)", reason, clean_url, jid or "n/a")
            await _pause_ms(max(300, delay_ms // 2))
        except Exception as exc:
            _raise_if_cancelled(exc)
            log.warning("Could not click unsave on saved list card %s: %s", href, exc)

        if card_has_applied:
            set_job_applied(cfg, {"url": href, "role": "Role", "company": "Company"}, applied=True)
            if jid:
                known_applied_ids.add(jid)
            if clean_url:
                known_applied_urls.add(clean_url)
        elif card_has_closed:
            record_deleted_job(cfg, {"url": href, "role": "Role", "company": "Company"})
            if jid:
                known_deleted_ids.add(jid)
            if clean_url:
                known_deleted_urls.add(clean_url)

    return unsaved_keys


async def _collect_paginated_job_links(
    page,
    cfg: Config,
    source: dict,
    url: str,
    delay_ms: int,
    login_wait: int,
    limit: int,
    *,
    max_pages: int = SAVED_LIST_MAX_PAGES,
) -> list[str]:
    """Stay on the saved/search list and follow Next until limit, last page, or max_pages."""
    if limit <= 0 or not await _open_listings_url(page, cfg, url, delay_ms, login_wait):
        return []
    contains = _as_list(source.get("link_contains")) or None
    found: list[str] = []
    seen: set[str] = set()

    from pipeline.jobs import (
        applied_job_urls,
        applied_linkedin_ids,
        deleted_job_urls,
        deleted_linkedin_ids,
        extract_linkedin_job_id,
    )

    known_applied_urls = applied_job_urls(cfg) if source.get("saved") else set()
    known_applied_ids = applied_linkedin_ids(cfg) if source.get("saved") else set()
    known_deleted_urls = deleted_job_urls(cfg) if source.get("saved") else set()
    known_deleted_ids = deleted_linkedin_ids(cfg) if source.get("saved") else set()

    pages = max(1, min(40, int(max_pages or SAVED_LIST_MAX_PAGES)))
    for page_n in range(1, pages + 1):
        if _stopped() or len(found) >= limit:
            break
        label = source.get("id") or "jobs"
        _notify_stage(f"Reading {label} (page {page_n})")
        await _scroll_listings(page)

        is_saved_linkedin = bool(source.get("saved")) and "linkedin.com" in (page.url or "").lower()
        unsaved_keys: set[str] = set()
        if is_saved_linkedin:
            unsaved_keys = await _clean_and_unsave_linkedin_saved_jobs(page, cfg, delay_ms)

        try:
            html = await page.content()
        except Exception as exc:
            _raise_if_cancelled(exc)
            break
        new = 0
        for link in collect_job_links(html, page.url, contains):
            key = link.lower()
            clean_link = link.split("?")[0].split("#")[0].lower()
            link_jid = extract_linkedin_job_id(link)
            if (
                key in seen
                or key in unsaved_keys
                or clean_link in unsaved_keys
                or (link_jid and link_jid in unsaved_keys)
                or (
                    is_saved_linkedin
                    and (
                        clean_link in known_applied_urls
                        or (link_jid and link_jid in known_applied_ids)
                        or clean_link in known_deleted_urls
                        or (link_jid and link_jid in known_deleted_ids)
                    )
                )
            ):
                continue
            seen.add(key)
            found.append(link)
            new += 1
            if len(found) >= limit:
                log.info("%s: %s unique posting link(s) across %s page(s)", label, len(found), page_n)
                return found
        if page_n >= pages:
            break
        if new == 0 and page_n > 1:
            break
        moved = await _click_next_listings_page(page, delay_ms)
        if not moved:
            break
    log.info("%s: %s unique posting link(s) across listing page(s)", source.get("id") or "jobs", len(found))
    return found


async def _collect_from_url(
    page,
    cfg: Config,
    source: dict,
    url: str,
    delay_ms: int,
    login_wait: int,
    max_per: int,
) -> list[dict]:
    if not await _open_listings_url(page, cfg, url, delay_ms, login_wait):
        return []
    await _scroll_listings(page)
    html = await page.content()
    contains = _as_list(source.get("link_contains")) or None
    links = collect_job_links(html, page.url, contains)
    listings = []
    saved = bool(source.get("saved"))
    for link in links[:max_per]:
        if _stopped():
            break
        item = await _extract_posting(page, cfg, source, link, delay_ms)
        if item:
            if saved:
                _mark_saved_listing(item, link)
            listings.append(item)
            _notify_listing(item)
    return listings


async def _collect_saved_jobs(page, cfg: Config, delay_ms: int, login_wait: int, max_per: int) -> list[dict]:
    """Jobs you already saved on LinkedIn/Indeed count as matches — no fit-gate drop."""
    try:
        cap = int(cfg.get("hunt.saved_jobs.max") or max_per)
    except (TypeError, ValueError):
        cap = max_per
    cap = max(1, min(80, cap))
    source = {
        "id": "saved jobs",
        "saved": True,
        "link_contains": ["/jobs/view/", "/viewjob", "jk="],
    }
    listings: list[dict] = []
    seen: set[str] = set()
    for url in saved_job_urls(cfg):
        if _stopped() or len(listings) >= cap:
            break
        log.info("Opening saved jobs %s", url)
        try:
            links = await _collect_paginated_job_links(
                page, cfg, source, url, delay_ms, login_wait, cap - len(listings)
            )
        except Exception as exc:
            _raise_if_cancelled(exc)
            log.warning("Saved jobs page failed %s: %s", url, exc)
            continue
        for link in links:
            if _stopped() or len(listings) >= cap:
                break
            item = await _extract_posting(page, cfg, source, link, delay_ms)
            if not item:
                continue
            _mark_saved_listing(item, link)
            key = (item.get("url") or link).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            listings.append(item)
            _notify_listing(item)
    return listings


async def _scroll_listings(page) -> None:
    try:
        for _ in range(3):
            if _stopped():
                return
            await page.evaluate("window.scrollBy(0, 1400)")
            if await _pause_ms(500):
                return
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception as exc:
        _raise_if_cancelled(exc)


def linkedin_credentials(cfg: Config) -> tuple[str, str]:
    email = (cfg.get("hunt.browser.logins.linkedin.email") or cfg.get("user.email") or "").strip()
    password = (cfg.get("hunt.browser.logins.linkedin.password") or "").strip()
    return email, password


async def _try_configured_login(page, cfg: Config, delay_ms: int) -> bool:
    current = (page.url or "").lower()
    if "linkedin.com" in current:
        return await _linkedin_login(page, cfg, delay_ms)
    return False


async def _linkedin_login(page, cfg: Config, delay_ms: int) -> bool:
    email, password = linkedin_credentials(cfg)
    if not email or not password:
        log.info("LinkedIn login skipped — set hunt.browser.logins.linkedin.password in config.yaml.")
        return False
    log.info("Filling LinkedIn login for %s (password not logged).", email)
    try:
        if "linkedin.com/login" not in (page.url or "").lower():
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
        if await _pause_ms(max(1200, delay_ms)):
            return False
        await _dismiss_login_overlays(page)
        if await _pause_ms(400):
            return False
        # LinkedIn mounts a hidden SSR form and a visible React form. .first hits the hidden one.
        filled_user = await _type_linkedin_field(page, "username", email)
        filled_pass = await _type_linkedin_field(page, "password", password)
        if not (filled_user and filled_pass):
            log.warning("LinkedIn visible login fields not found. Complete sign-in in the Camoufox window.")
            return False
        log.info("Typed LinkedIn credentials into the visible form.")
        await _dismiss_login_overlays(page)
        clicked = await _click_linkedin_sign_in(page)
        if not clicked:
            log.warning("LinkedIn Sign in button not found.")
            return False
        try:
            await page.wait_for_function(
                "() => !location.pathname.includes('/login') || location.pathname.includes('/checkpoint')",
                timeout=20000,
            )
        except Exception as exc:
            _raise_if_cancelled(exc)
            if await _pause_ms(max(2000, delay_ms * 2)):
                return False
        url = (page.url or "").lower()
        wait = int(cfg.get("hunt.browser.login_wait_seconds", 300) or 0)
        if any(token in url for token in ("/checkpoint", "/challenge", "captcha")) or await _needs_login(page):
            ok = await _wait_for_manual_auth(
                page,
                wait,
                "Complete extra verification — use the Camoufox panel",
            )
            if not ok:
                return False
        still_login = "/login" in (page.url or "").lower() and "/checkpoint" not in (page.url or "").lower()
        if still_login:
            log.warning("Still on LinkedIn login after submit. Complete sign-in in the Camoufox panel.")
            return False
        log.info("LinkedIn session looks signed in.")
        return True
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.warning("LinkedIn auto-login failed: %s", exc)
        return False


async def _type_linkedin_field(page, kind: str, value: str) -> bool:
    """Fill the visible React login field, not the hidden SSR duplicate."""
    if kind == "username":
        selectors = [
            "input#username:visible",
            "input[name='session_key']:visible",
            "input[autocomplete='username']:visible",
        ]
        labels = ("Email or phone", "Email")
    else:
        selectors = [
            "input#password:visible",
            "input[name='session_password']:visible",
            "input[autocomplete='current-password']:visible",
            "input[type='password']:visible",
        ]
        labels = ("Password",)

    for selector in selectors:
        loc = page.locator(selector)
        try:
            if await loc.count() == 0:
                continue
            await loc.first.click(timeout=4000)
            await loc.first.fill(value, timeout=4000)
            return True
        except Exception:
            continue

    for label in labels:
        loc = page.get_by_label(label)
        try:
            n = await loc.count()
            for i in range(n - 1, -1, -1):
                item = loc.nth(i)
                if await item.is_visible():
                    await item.click(timeout=4000)
                    await item.fill(value, timeout=4000)
                    return True
        except Exception:
            continue

    focused = await page.evaluate(
        """(kind) => {
          const visible = (el) =>
            el instanceof HTMLElement &&
            el.offsetParent !== null &&
            el.getClientRects().length > 0 &&
            !el.disabled;
          const selectors =
            kind === "username"
              ? ["input#username", "input[name='session_key']", "input[autocomplete='username']"]
              : ["input#password", "input[name='session_password']", "input[type='password']"];
          let field = null;
          for (const sel of selectors) {
            field = Array.from(document.querySelectorAll(sel)).find(visible) || null;
            if (field) break;
          }
          if (!field) return false;
          field.focus();
          field.click();
          if (typeof field.select === "function") field.select();
          return true;
        }""",
        kind,
    )
    if not focused:
        return False
    try:
        await page.keyboard.press("Meta+A")
    except Exception:
        try:
            await page.keyboard.press("Control+A")
        except Exception:
            pass
    await page.keyboard.press("Backspace")
    await page.keyboard.type(value, delay=25)
    return True


async def _click_linkedin_sign_in(page) -> bool:
    clicked = await page.evaluate(
        """() => {
          const visible = (el) =>
            el instanceof HTMLElement && el.offsetParent !== null && el.getClientRects().length > 0;
          const buttons = Array.from(document.querySelectorAll("button[type='submit'], button"));
          const btn = buttons.find((el) => {
            const text = (el.textContent || "").replace(/\\s+/g, " ").trim();
            return visible(el) && /^Sign in$/i.test(text);
          });
          if (!btn) return false;
          btn.click();
          return true;
        }"""
    )
    if clicked:
        return True
    try:
        loc = page.get_by_role("button", name="Sign in", exact=True)
        n = await loc.count()
        for i in range(n):
            item = loc.nth(i)
            if await item.is_visible():
                await item.click(force=True, timeout=4000)
                return True
    except Exception:
        pass
    return False


async def _dismiss_login_overlays(page) -> None:
    """Google One Tap and similar iframes block the email/password fields."""
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        await page.evaluate(
            """() => {
              document.querySelectorAll(
                '#credential_picker_container, iframe[src*="accounts.google.com"], iframe[id*="gsi"], iframe[src*="google.com/signin"]'
              ).forEach((el) => el.remove());
            }"""
        )
    except Exception:
        pass
    closer = page.locator(
        "#credential_picker_container [aria-label='Close'], "
        "iframe[src*='accounts.google.com']"
    )
    try:
        if await closer.count():
            await page.keyboard.press("Escape")
    except Exception:
        pass


async def _fill_visible(page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="attached", timeout=4000)
            await loc.fill(value, force=True, timeout=4000)
            return True
        except Exception:
            try:
                await loc.click(force=True, timeout=2000)
                await loc.fill(value, force=True, timeout=4000)
                return True
            except Exception:
                continue
    return False


async def _click_first(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if await loc.count() == 0:
                continue
            await loc.first.click(force=True, timeout=4000)
            return True
        except Exception:
            continue
    return False


async def _needs_login(page) -> bool:
    current = (page.url or "").lower()
    if any(token in current for token in ("/login", "authwall", "/checkpoint", "uas/login", "account/login")):
        return True
    try:
        body = (await page.content())[:12000].lower()
    except Exception:
        return False
    if "linkedin.com" in current and ("sign in" in body or "join now" in body) and "/jobs/view/" not in current:
        return True
    if "google." in current and (
        "consent" in current or "before you continue" in body or "unusual traffic" in body
    ):
        return True
    if "indeed." in current and "sign in" in body and "viewjob" not in current:
        return False
    return False


async def _unsave_linkedin_posting_page(page, delay_ms: int) -> bool:
    """Click the Saved button on a LinkedIn job posting page to unsave it."""
    js = """
    () => {
      const candidates = Array.from(document.querySelectorAll(
        'button.jobs-save-button, button[aria-label*="unsave" i], button[data-control-name="save_job"]'
      ));
      for (const btn of candidates) {
        const text = (btn.innerText || '').trim().toLowerCase();
        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
        if (text === 'saved' || label.includes('unsave')) {
          btn.click();
          return true;
        }
      }
      for (const btn of Array.from(document.querySelectorAll('button'))) {
        const text = (btn.innerText || '').trim().toLowerCase();
        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
        if (text === 'saved' || label.startsWith('unsave')) {
          btn.click();
          return true;
        }
      }
      return false;
    }
    """
    try:
        clicked = bool(await page.evaluate(js))
        if clicked:
            log.info("Camoufox clicked Unsave on LinkedIn posting page: %s", page.url)
            await _pause_ms(max(400, delay_ms))
            return True
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.debug("Unsave posting page evaluate error: %s", exc)
    return False


async def _extract_posting(page, cfg: Config, source: dict, url: str, delay_ms: int) -> dict | None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if await _pause_ms(max(400, delay_ms)):
            return None
        if await _needs_login(page):
            await _try_configured_login(page, cfg, delay_ms)
            wait = int(cfg.get("hunt.browser.login_wait_seconds", 300) or 0)
            if await _needs_login(page):
                await _wait_for_manual_auth(
                    page,
                    wait,
                    "Sign in or extra verification — use the Camoufox panel",
                )
            if _stopped():
                return None
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if await _pause_ms(max(400, delay_ms)):
                return None
        html = await page.content()
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.info("Skip %s (%s)", url, exc)
        return None

    final_url = canonicalize_job_url(page.url or url)
    if not is_job_posting_url(final_url):
        log.info("Skip non-job page %s", final_url)
        return None

    title = await _first_text(page, _as_list(source.get("title_selectors")) or list(DEFAULT_TITLE_SELECTORS))
    company = await _first_text(
        page, _as_list(source.get("company_selectors")) or list(DEFAULT_COMPANY_SELECTORS)
    )
    jd = await _first_text(page, _as_list(source.get("jd_selectors")) or list(DEFAULT_JD_SELECTORS))
    meta = parse_posting_meta(html, url)
    if is_placeholder_company(company):
        company = meta.get("company") or ""
    if not title:
        title = meta.get("role") or ""
    if not jd:
        jd = meta.get("jd") or html_to_text(html)
    if not title:
        raw_title = await page.title()
        tab_company, tab_role = company_role_from_title(raw_title or "")
        if is_placeholder_company(company):
            company = tab_company
        title = tab_role or (raw_title or "").split("|")[0].split(" - ")[0].strip()
    if not title:
        return None
    if is_placeholder_company(company):
        company = _company_from_host(url)
    if is_placeholder_company(company):
        company = ""
    loc = await _first_text(
        page,
        _as_list(source.get("location_selectors"))
        or [
            ".job-details-jobs-unified-top-card__primary-description-container",
            ".job-details-jobs-unified-top-card__bullet",
            ".job-details-jobs-unified-top-card__tertiary-description-container",
            ".tvm__text--low-emphasis",
            ".topcard__flavor--bullet",
            ".topcard__flavor-row",
            ".jobsearch-JobInfoHeader-subtitle",
            "[data-testid='job-location']",
        ],
    )
    if loc and "·" in loc:
        # e.g. "Montreal, QC · Reposted 4 days ago · Over 100 people clicked apply"
        loc = loc.split("·")[0].strip()
    loc = loc or meta.get("location") or hunt_location(cfg)
    await _wait_for_apply_controls(page, delay_ms)
    try:
        html = await page.content()
    except Exception as exc:
        _raise_if_cancelled(exc)
    apply = await apply_target_from_page(page, final_url, html=html)
    apply_url = apply.apply_url
    apply_kind = apply.apply_kind
    listing = {
        "company": (company or "Unknown").strip(),
        "role": title.strip()[:160],
        "url": final_url,
        "location": (loc or "").strip(),
        "jd": (jd or "").strip(),
        "source": f"camoufox:{_source_name(source)}",
        "apply_url": apply_url,
        "apply_kind": apply_kind,
    }
    from pipeline.jobs import decorate_listing

    listing = decorate_listing(listing)
    if is_directory_or_salary_listing(listing):
        log.info("Skip non-job page %s", final_url)
        return None

    if "linkedin.com" in final_url.lower():
        from pipeline.jobs import (
            applied_job_urls,
            applied_linkedin_ids,
            deleted_job_urls,
            deleted_linkedin_ids,
            extract_linkedin_job_id,
            record_deleted_job,
            set_job_applied,
        )

        post_jid = extract_linkedin_job_id(final_url)
        post_clean = final_url.split("?")[0].split("#")[0].lower()
        post_has_applied = bool(
            re.search(r"\b(applied|you applied)\b", html, re.I)
            or re.search(r"\bapplied\s+\d+\s*(?:day|week|month|hour|m|d|w)s?\s+ago\b", html, re.I)
        )
        post_has_closed = bool(
            re.search(r"\b(no longer accepting applications|this job is closed|job closed)\b", html, re.I)
        )
        post_applied = (
            (post_jid and post_jid in applied_linkedin_ids(cfg))
            or (post_clean and post_clean in applied_job_urls(cfg))
            or post_has_applied
        )
        post_deleted = (
            (post_jid and post_jid in deleted_linkedin_ids(cfg))
            or (post_clean and post_clean in deleted_job_urls(cfg))
            or post_has_closed
        )

        if post_applied or post_deleted:
            reason = "applied" if post_applied else "deleted"
            await _unsave_linkedin_posting_page(page, delay_ms)
            if post_has_applied:
                set_job_applied(cfg, listing, applied=True)
            elif post_has_closed:
                record_deleted_job(cfg, listing)
            if source.get("saved"):
                log.info("Camoufox unsaved and skipped %s job on posting page: %s", reason, final_url)
                return None
    return listing


def _company_from_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "linkedin.com" in host or "indeed." in host:
        return ""
    for prefix in ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"):
        if prefix in host:
            path = urlparse(url).path.strip("/").split("/")
            if path and path[0]:
                return path[0].replace("-", " ").title()
    return ""


_APPLY_READY_JS = """() => {
  const text = document.body ? document.body.innerText : "";
  if (/easy apply/i.test(text) || /apply on company/i.test(text)) return true;
  return Boolean(document.querySelector(
    ".jobs-apply-button, [data-live-test-job-apply-button], .indeed-apply-button, .jobsearch-IndeedApplyButton"
  ));
}"""

_APPLY_TARGET_JS = """() => {
  const unescapeUrl = (value) => String(value || "")
    .replace(/\\\\u0026/g, "&")
    .replace(/\\\\u002f/g, "/")
    .replace(/\\\\//g, "/");
  const hostOf = (value) => {
    try { return new URL(value).hostname.replace(/^www\\./, "").toLowerCase(); }
    catch { return ""; }
  };
  const offsite = (value) => {
    const host = hostOf(value);
    return Boolean(host) && !/(^|\\.)linkedin\\.com$|(^|\\.)indeed\\./i.test(host);
  };
  const unwrap = (value) => {
    const raw = unescapeUrl(value);
    try {
      const parsed = new URL(raw);
      const host = hostOf(raw);
      if (/(^|\\.)linkedin\\.com$|(^|\\.)lnkd\\.in$/i.test(host)) {
        const inner = parsed.searchParams.get("url") || parsed.searchParams.get("dest") || parsed.searchParams.get("redirectUrl");
        if (inner && inner.startsWith("http") && offsite(inner)) return inner;
      }
    } catch {}
    return raw;
  };
  const html = document.documentElement ? document.documentElement.innerHTML : "";
  const textOf = (el) =>
    `${el.getAttribute("aria-label") || ""} ${el.textContent || ""}`.replace(/\\s+/g, " ").trim();
  const isApply = (text) =>
    /^(easy apply|apply now|apply on company website|apply)$/i.test(text) ||
    /apply on company/i.test(text);
  let href = "";
  let easyApply = false;
  for (const el of document.querySelectorAll("button, a, [role='button']")) {
    const text = textOf(el);
    if (text.length > 120) continue;
    if (/easy apply|indeed apply/i.test(text)) easyApply = true;
  }
  const keyRe = /"(?:companyApplyUrl|companyApplicationUrl|externalApplyUrl|applyStartUrl|applyConnectUrl|jobApplyUrl)"\\s*:\\s*"(https?:[^"]+)"/gi;
  let match;
  while ((match = keyRe.exec(html))) {
    const found = unwrap(match[1]);
    if (offsite(found)) { href = found; break; }
  }
  if (!href) {
    const voyager = html.match(/OffsiteApply"\\s*:\\s*\\{[\\s\\S]{0,1500}?"(?:companyApplyUrl|companyApplicationUrl|applyUrl)"\\s*:\\s*"(https?:[^"]+)"/i);
    if (voyager && offsite(unwrap(voyager[1]))) href = unwrap(voyager[1]);
  }
  if (!href) {
    for (const a of document.querySelectorAll("a[href]")) {
      const found = unwrap(a.href || "");
      if (!found || /^(javascript:|#)/i.test(found)) continue;
      if (offsite(found) && (isApply(textOf(a)) || /apply/i.test(a.className || ""))) {
        href = found;
        break;
      }
    }
  }
  if (!href) {
    for (const a of document.querySelectorAll("a[href]")) {
      const found = unwrap(a.href || "");
      if (!found || /^(javascript:|#)/i.test(found)) continue;
      if (isApply(textOf(a))) { href = found; break; }
    }
  }
  if (!href) {
    const code = document.querySelector("code#applyUrl, code[id*='applyUrl' i]");
    if (code) {
      const raw = (code.innerHTML || code.textContent || "").replace(/<!--\\s*"?|\\s*"?-->/g, " ").trim();
      const found = unwrap(raw.split(/\\s+/)[0]);
      if (found.startsWith("http")) href = found;
    }
  }
  return { href, easyApply };
}"""


async def _wait_for_apply_controls(page, delay_ms: int) -> None:
    """Wait for LinkedIn/Indeed Apply to hydrate. Never clicks it."""
    timeout = max(5000, int(delay_ms or 0) * 3)
    try:
        await page.wait_for_function(_APPLY_READY_JS, timeout=timeout)
    except Exception as exc:
        _raise_if_cancelled(exc)
    await _pause_ms(max(300, min(int(delay_ms or 0), 1200)))


async def _apply_live_from_page(page) -> dict:
    try:
        raw = await page.evaluate(_APPLY_TARGET_JS)
    except Exception as exc:
        _raise_if_cancelled(exc)
        return {"href": "", "easyApply": False}
    if isinstance(raw, dict):
        return {
            "href": str(raw.get("href") or "").strip(),
            "easyApply": bool(raw.get("easyApply")),
        }
    return {"href": str(raw or "").strip(), "easyApply": False}


async def _apply_href_from_page(page) -> str:
    """Read the Apply link href. Never clicks it."""
    return str((await _apply_live_from_page(page)).get("href") or "").strip()


async def apply_target_from_page(page, posting_url: str, html: str = ""):
    """Read Easy Apply vs company form from the live page. Never clicks Apply."""
    from pipeline.apply_url import (
        ApplyTarget,
        canonicalize_form_url,
        extract_apply_from_html,
        is_aggregator_url,
        is_ats_form_url,
        unwrap_outbound_url,
    )

    posting = (posting_url or "").strip()
    blob = html or ""
    if not blob:
        try:
            blob = await page.content()
        except Exception as exc:
            _raise_if_cancelled(exc)
            blob = ""
    target = extract_apply_from_html(blob, posting)
    live = await _apply_live_from_page(page)
    href = unwrap_outbound_url(live.get("href") or "")
    if href and not is_aggregator_url(href):
        kind = "ats" if is_ats_form_url(href) else "company"
        return ApplyTarget(canonicalize_form_url(href), kind, "camoufox")
    if href and (not target.apply_url or is_aggregator_url(target.apply_url)):
        followed = await _follow_apply_href(page, href)
        followed = unwrap_outbound_url(followed)
        if followed and not is_aggregator_url(followed):
            kind = "ats" if is_ats_form_url(followed) else "company"
            return ApplyTarget(canonicalize_form_url(followed), kind, "camoufox")
    if target.apply_kind in {"ats", "company"} and target.apply_url and not is_aggregator_url(target.apply_url):
        return ApplyTarget(target.apply_url, target.apply_kind, "camoufox")
    if live.get("easyApply") or target.apply_kind == "easy_apply":
        return ApplyTarget(posting, "easy_apply", "camoufox")
    if target.apply_url and not is_aggregator_url(target.apply_url):
        return target
    return target


async def _follow_apply_href(page, url: str) -> str:
    """HTTP-follow an apply href with the same cookies. Does not click Apply."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return ""
    try:
        response = await page.request.get(raw, max_redirects=12, timeout=15000)
        final = (response.url or raw).strip()
        return final
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.info("Apply href follow failed for %s: %s", url, exc)
        return ""


async def _open_posting_for_apply(page, cfg: Config, url: str, delay_ms: int) -> str:
    """Navigate to a posting and wait for Apply. Empty string if stop/login-fail."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return ""
    await page.goto(raw, wait_until="domcontentloaded", timeout=45000)
    if await _pause_ms(max(400, delay_ms)):
        return ""
    if await _needs_login(page):
        await _try_configured_login(page, cfg, delay_ms)
        wait = int(cfg.get("hunt.browser.login_wait_seconds", 300) or 0)
        if await _needs_login(page):
            await _wait_for_manual_auth(
                page,
                wait,
                "Sign in or extra verification — use the Camoufox panel",
            )
        if _stopped() or await _needs_login(page):
            return ""
        await page.goto(raw, wait_until="domcontentloaded", timeout=45000)
        if await _pause_ms(max(400, delay_ms)):
            return ""
    await _wait_for_apply_controls(page, delay_ms)
    return (page.url or raw).strip()


async def resolve_apply_in_browser(cfg: Config, url: str):
    """Open a signed-in posting and read Easy Apply or the company form. Never clicks Apply."""
    from pipeline.apply_url import ApplyTarget

    raw = (url or "").strip()
    if not raw.startswith("http"):
        return ApplyTarget("", "unknown", "")
    results = await resolve_apply_jobs_in_browser(cfg, [{"url": raw}])
    if results:
        item = results[0]
        return ApplyTarget(item.get("apply_url") or "", item.get("apply_kind") or "unknown", "camoufox")
    return ApplyTarget("", "unknown", "")


async def resolve_apply_jobs_in_browser(
    cfg: Config,
    jobs: list[dict],
    on_progress=None,
    on_stage=None,
    should_stop=None,
) -> list[dict]:
    """Visit each posting once and read Apply. Does not tailor CVs or click Apply."""
    from pipeline.apply_url import ApplyTarget

    listing_token = _on_listing.set(None)
    stage_token = _on_stage.set(on_stage)
    stop_token = _should_stop.set(should_stop)
    pending = [dict(job) for job in jobs if str(job.get("url") or "").startswith("http")]
    if not pending:
        _on_listing.reset(listing_token)
        _on_stage.reset(stage_token)
        _should_stop.reset(stop_token)
        return []
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        log.error("Camoufox is not installed. Run: python3 -m pip install camoufox && python3 -m camoufox fetch")
        _on_listing.reset(listing_token)
        _on_stage.reset(stage_token)
        _should_stop.reset(stop_token)
        return []
    delay_ms = int(cfg.get("hunt.browser.page_delay_ms", 1500) or 1500)
    launch = _camoufox_launch(cfg)
    out: list[dict] = []
    try:
        _notify_stage("Opening job pages in Camoufox to read Apply links")
        async with AsyncCamoufox(**launch) as session:
            page = await session.new_page()
            for job in pending:
                if _stopped():
                    break
                raw = (job.get("url") or "").strip()
                if on_progress:
                    try:
                        on_progress(job, None)
                    except Exception as exc:
                        _raise_if_cancelled(exc)
                try:
                    final = await _open_posting_for_apply(page, cfg, raw, delay_ms)
                    if not final:
                        target = ApplyTarget("", "unknown", "login" if await _needs_login(page) else "")
                    else:
                        target = await apply_target_from_page(page, final)
                except Exception as exc:
                    _raise_if_cancelled(exc)
                    log.info("Browser apply resolve failed for %s: %s", raw, exc)
                    target = ApplyTarget("", "unknown", "")
                item = {
                    **job,
                    "apply_url": target.apply_url,
                    "apply_kind": target.apply_kind,
                }
                out.append(item)
                if on_progress:
                    try:
                        on_progress(item, target)
                    except Exception as exc:
                        _raise_if_cancelled(exc)
        return out
    except Exception as exc:
        _raise_if_cancelled(exc)
        log.info("Browser apply resolve failed: %s", exc)
        return out
    finally:
        _on_listing.reset(listing_token)
        _on_stage.reset(stage_token)
        _should_stop.reset(stop_token)


async def _first_text(page, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if await loc.count() == 0:
                continue
            text = (await loc.first.inner_text()).strip()
            if text:
                return text
        except Exception:
            continue
    return ""
