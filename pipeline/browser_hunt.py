"""Browse job boards and ATS pages with Camoufox. Never clicks Apply or Submit."""

from __future__ import annotations

import asyncio
import logging
import os
import re
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
from pipeline.search import html_to_text, hunt_queries, target_markets, target_roles

log = logging.getLogger(__name__)

_on_listing: ContextVar = ContextVar("on_listing", default=None)
_on_stage: ContextVar = ContextVar("on_stage", default=None)
_should_stop: ContextVar = ContextVar("should_stop", default=None)


def _notify_listing(item: dict | None) -> None:
    callback = _on_listing.get()
    if not callback or not item:
        return
    try:
        callback(item)
    except Exception:
        log.debug("on_listing failed", exc_info=True)


def _notify_stage(line: str) -> None:
    callback = _on_stage.get()
    if not callback or not (line or "").strip():
        return
    try:
        callback(line.strip())
    except Exception:
        log.debug("on_stage failed", exc_info=True)


def _stopped() -> bool:
    callback = _should_stop.get()
    try:
        return bool(callback and callback())
    except Exception:
        return False


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
    "ats.rippling.com/",
    "wellfound.com/jobs/",
    "themuse.com/jobs/",
    "jobs.jobvite.com/",
    "apply.workable.com/",
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


def hunt_location(cfg: Config) -> str:
    city = (cfg.get("user.city") or "").strip()
    country = (cfg.get("user.country") or "").strip()
    if city and country:
        return f"{city}, {country}"
    markets = target_markets(cfg)
    if markets:
        if city:
            return f"{city}, {markets[0]}"
        return markets[0]
    return city or country or "Remote"


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
                    log.warning("Camoufox source %s failed: %s", _source_name(source), exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
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
        # Headed Firefox needs a display. Camoufox "virtual" starts its own Xvfb
        # (compose Xvfb is also started; a bare `Xvfb &` used to die on exec).
        headless = "virtual"
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
        for board_url in urls[:15]:
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
    for query in queries:
        if _stopped():
            break
        search_url = fill_search_url(template, cfg, query)
        for item in await _collect_from_url(page, cfg, source, search_url, delay_ms, login_wait, max_per):
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
    max_queries = max(1, min(4, int(source.get("max_queries") or 1)))
    group_size = max(1, min(8, int(source.get("group_size") or 1)))
    queries = hunt_queries(cfg)[:max_queries]
    location = hunt_location(cfg)
    groups = [ats_ops[i : i + group_size] for i in range(0, len(ats_ops), group_size)]
    listings = []
    seen = set()
    per_page = max(1, min(max_per, int(source.get("max_results") or 4)))
    for query in queries:
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


async def _collect_from_url(
    page,
    cfg: Config,
    source: dict,
    url: str,
    delay_ms: int,
    login_wait: int,
    max_per: int,
) -> list[dict]:
    if _stopped():
        return []
    log.info("Camoufox open %s", url)
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(max(400, delay_ms))
    if await _needs_login(page):
        signed_in = await _try_configured_login(page, cfg, delay_ms)
        if not signed_in:
            wait = min(login_wait, 45) if login_wait else 0
            if wait:
                log.warning(
                    "LinkedIn not signed in yet. You can finish Sign in in the Camoufox window. Waiting %ss.",
                    wait,
                )
                _notify_stage("Waiting for LinkedIn sign-in")
                for _ in range(wait):
                    if _stopped():
                        return []
                    await page.wait_for_timeout(1000)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(max(400, delay_ms))

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
                item["saved"] = True
                host = urlparse(item.get("url") or link).netloc.lower()
                board = "linkedin" if "linkedin.com" in host else "indeed" if "indeed." in host else "board"
                item["source"] = f"saved:{board}"
            listings.append(item)
            _notify_listing(item)
    return listings


async def _collect_saved_jobs(page, cfg: Config, delay_ms: int, login_wait: int, max_per: int) -> list[dict]:
    """Jobs you already saved on LinkedIn/Indeed count as matches — no fit-gate drop."""
    try:
        cap = int(cfg.get("hunt.saved_jobs.max") or max_per)
    except (TypeError, ValueError):
        cap = max_per
    cap = max(1, min(20, cap))
    source = {
        "id": "saved",
        "saved": True,
        "link_contains": ["/jobs/view/", "/viewjob", "jk="],
    }
    listings: list[dict] = []
    seen: set[str] = set()
    for url in saved_job_urls(cfg):
        log.info("Opening saved jobs %s", url)
        try:
            found = await _collect_from_url(page, cfg, source, url, delay_ms, login_wait, cap)
        except Exception as exc:
            log.warning("Saved jobs page failed %s: %s", url, exc)
            continue
        for item in found:
            key = (item.get("url") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            listings.append(item)
            if len(listings) >= cap:
                return listings
    return listings


async def _scroll_listings(page) -> None:
    try:
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 1400)")
            await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


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
        await page.wait_for_timeout(max(1200, delay_ms))
        await _dismiss_login_overlays(page)
        await page.wait_for_timeout(400)
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
        except Exception:
            await page.wait_for_timeout(max(2000, delay_ms * 2))
        url = (page.url or "").lower()
        if any(token in url for token in ("/checkpoint", "/challenge", "captcha")):
            log.warning("LinkedIn asked for extra verification. Complete it in the Camoufox window.")
            wait = int(cfg.get("hunt.browser.login_wait_seconds", 120) or 0)
            if wait:
                await page.wait_for_timeout(wait * 1000)
        still_login = "/login" in (page.url or "").lower() and "/checkpoint" not in (page.url or "").lower()
        if still_login:
            log.warning("Still on LinkedIn login after submit. Complete sign-in in the window if a prompt remains.")
            return False
        log.info("LinkedIn session looks signed in.")
        return True
    except Exception as exc:
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


async def _extract_posting(page, cfg: Config, source: dict, url: str, delay_ms: int) -> dict | None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(max(400, delay_ms))
        if await _needs_login(page):
            await _try_configured_login(page, cfg, delay_ms)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(max(400, delay_ms))
        html = await page.content()
    except Exception as exc:
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
        or [".job-details-jobs-unified-top-card__bullet", ".jobsearch-JobInfoHeader-subtitle", "[data-testid='job-location']"],
    )
    loc = loc or meta.get("location") or hunt_location(cfg)
    listing = {
        "company": (company or "Unknown").strip(),
        "role": title.strip()[:160],
        "url": final_url,
        "location": (loc or "").strip(),
        "jd": (jd or "").strip(),
        "source": f"camoufox:{_source_name(source)}",
    }
    if is_directory_or_salary_listing(listing):
        log.info("Skip non-job page %s", final_url)
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
