"""Find job listings that match the user's config profile."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import quote_plus

import requests
import yaml
from bs4 import BeautifulSoup

from pipeline.config import Config
from pipeline.jobs import BLOCKED_HOSTS, fetch_jd, is_directory_or_salary_listing, slug

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; job-search-pipeline/1.0)"
JsonFetcher = Callable[[str], Optional[Union[dict, list]]]
JdFetcher = Callable[[str], Optional[str]]

_json_fetcher: JsonFetcher | None = None
_jd_fetcher: JdFetcher | None = None


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
    return "\n".join(line for line in lines if line)


def blocked_url(url: str) -> bool:
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return any(host == blocked or host.endswith(blocked) for blocked in BLOCKED_HOSTS)


def target_roles(cfg: Config) -> list[str]:
    raw = cfg.get("career.target_roles") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def target_markets(cfg: Config) -> list[str]:
    raw = cfg.get("career.target_markets") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def hunt_limit(cfg: Config, override: int | None = None) -> int | None:
    """Safety ceiling for how many matches to tailor. None/0 = every match."""
    if override is not None:
        raw = override
    else:
        raw = cfg.get("hunt.max_jobs", cfg.get("pipeline.hunt_max_jobs", 0))
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def greenhouse_boards(cfg: Config) -> list[str]:
    raw = cfg.get("hunt.greenhouse_boards") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip().lower() for item in raw if str(item).strip()]


def _as_list(raw, default: list[str] | None = None) -> list[str]:
    if raw is None:
        return list(default or [])
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(item).strip() for item in raw if str(item).strip()]


def years_experience(cfg: Config) -> int | None:
    raw = cfg.get("hunt.years_experience", cfg.get("career.years_experience"))
    try:
        years = int(raw)
    except (TypeError, ValueError):
        return None
    return years if years > 0 else None


def years_buffer(cfg: Config) -> int:
    raw = cfg.get("hunt.years_buffer", 2)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 2


def years_cap(cfg: Config) -> int | None:
    experience = years_experience(cfg)
    if experience is None:
        return None
    return experience + years_buffer(cfg)


def exclude_levels(cfg: Config) -> list[str]:
    raw = cfg.get("hunt.exclude_levels")
    if raw is None and (cfg.get("career.stage") or "").lower() in {"senior", "mid"}:
        return ["intern", "internship", "junior", "new grad", "new-grad", "entry level"]
    return [item.lower() for item in _as_list(raw)]


def exclude_title_tokens(cfg: Config) -> list[str]:
    raw = cfg.get("hunt.exclude_title_tokens")
    if raw is None:
        return [
            "manager",
            "director",
            "head of",
            "vp of",
            "vice president",
            "qa",
            "quality assurance",
            "quality engineer",
        ]
    return [item.lower() for item in _as_list(raw)]


def require_any(cfg: Config) -> list[str]:
    return [item.lower() for item in _as_list(cfg.get("hunt.require_any"))]


def preferred_skills(cfg: Config) -> list[str]:
    return [item.lower() for item in _as_list(cfg.get("hunt.preferred_skills"))]


def reject_skills(cfg: Config) -> list[str]:
    return [item.lower() for item in _as_list(cfg.get("hunt.reject_skills"))]


def exclude_companies(cfg: Config) -> list[str]:
    return [item.lower() for item in _as_list(cfg.get("hunt.exclude_companies"))]


def company_is_excluded(listing: dict, cfg: Config) -> bool:
    """True if company name or job URL matches hunt.exclude_companies."""
    company = (listing.get("company") or "").lower()
    url = (listing.get("url") or "").lower()
    hay = f"{company} {url}"
    for name in exclude_companies(cfg):
        if phrase_in(hay, name):
            return True
        tokens = [word for word in re.split(r"\W+", name) if len(word) >= 4]
        if any(phrase_in(hay, token) for token in tokens):
            return True
    return False


WEAK_ROLE_WORDS = {"engineer", "engineering", "manager", "specialist", "analyst", "developer", "lead", "staff"}
IC_TITLE_HINTS = (
    "engineer",
    "developer",
    "programmer",
    "swe",
    "sde",
    "mts",
    "technical staff",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    "devops",
    "sre",
    "forward deployed",
)

YEAR_RANGE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*years?",
    re.I,
)
YEAR_PLUS = re.compile(r"(\d{1,2})\s*\+\s*years?", re.I)
YEAR_MIN = re.compile(r"(?:at least|minimum(?: of)?|min(?:imum)?)\s+(\d{1,2})\s*years?", re.I)
YEAR_EXP = re.compile(r"(\d{1,2})\s*years?\s+(?:of\s+)?(?:experience|exp\.?|professional)", re.I)


def phrase_in(text: str, phrase: str) -> bool:
    needle = (phrase or "").strip().lower()
    if not needle:
        return False
    hay = text.lower()
    if re.search(r"[.\\+#]", needle) or " " in needle:
        return needle in hay
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))


def _allowed_by_targets(phrase: str, targets: list[str]) -> bool:
    return any(phrase in target for target in targets)


def parse_required_years(text: str) -> int | None:
    """Strictest years-of-experience floor found in a posting. Ignores large metrics like 10000."""
    found: list[int] = []
    rest = text or ""
    for match in YEAR_RANGE.finditer(rest):
        found.append(min(int(match.group(1)), int(match.group(2))))
    stripped = YEAR_RANGE.sub(" ", rest)
    for pattern in (YEAR_PLUS, YEAR_MIN, YEAR_EXP):
        for match in pattern.finditer(stripped):
            found.append(int(match.group(1)))
    found = [years for years in found if 1 <= years <= 20]
    return max(found) if found else None


def _role_words(role: str) -> list[str]:
    skip = {"and", "the", "for", "with"}
    return [word for word in re.split(r"\W+", role.lower()) if len(word) > 2 and word not in skip]


def hunt_queries(cfg: Config) -> list[str]:
    """LinkedIn/Indeed/Google keywords. Skill-first so odd titles still surface."""
    configured = _as_list(cfg.get("hunt.browser.queries"))
    if configured:
        return configured[:4]
    roles = target_roles(cfg) or ["software engineer"]
    skills = preferred_skills(cfg)
    out: list[str] = []
    seen: set[str] = set()
    for item in (*(skills[:1]), *(roles[:1])):
        text = (item or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out[:2] or ["software engineer"]


def score_listing(listing: dict, cfg: Config) -> int:
    """Higher is a closer stack/location match. 0 means do not tailor this posting.

    Titles are vetoes (intern, manager, Java-in-title) and a ranking boost.
    Keep/drop after the JD loads is driven by preferred_skills / require_any.
    """
    url = listing.get("url") or ""
    if is_directory_or_salary_listing(listing):
        return 0
    if company_is_excluded(listing, cfg):
        return 0
    if listing.get("saved"):
        if not (listing.get("role") or "").strip():
            return 0
        if blocked_url(url) and not (listing.get("jd") or "").strip():
            return 0
        return 50
    if blocked_url(url) and not (listing.get("jd") or "").strip():
        return 0
    title = (listing.get("role") or "").lower()
    loc = (listing.get("location") or "").lower()
    jd = (listing.get("jd") or "").lower()
    blob = f"{title} {loc}"
    haystack = f"{title}\n{jd}"
    targets = [role.lower() for role in target_roles(cfg)]

    for level in exclude_levels(cfg):
        if _level_blocked_in_title(title, level, targets):
            return 0
    for token in exclude_title_tokens(cfg):
        if phrase_in(title, token) and not _allowed_by_targets(token, targets):
            return 0
    for skill in reject_skills(cfg):
        if phrase_in(title, skill):
            return 0

    title_score = 0
    for role in target_roles(cfg):
        words = _role_words(role)
        strong = [word for word in words if word not in WEAK_ROLE_WORDS]
        if role.lower() in title:
            title_score = max(title_score, 5)
        elif words and all(word in title for word in words):
            title_score = max(title_score, 4)
        elif strong and any(word in title for word in strong):
            title_score = max(title_score, 2)
    ic_hint = any(phrase_in(title, hint) for hint in IC_TITLE_HINTS)

    if jd:
        needed = parse_required_years(jd)
        cap = years_cap(cfg)
        if needed is not None and cap is not None and needed > cap:
            return 0
        must = require_any(cfg)
        if must and not any(phrase_in(haystack, skill) for skill in must):
            return 0
        rejected = reject_skills(cfg)
        core = must or [skill for skill in preferred_skills(cfg) if skill not in rejected]
        for skill in rejected:
            if not phrase_in(jd, skill):
                continue
            attached = re.search(
                rf"(?:{re.escape(skill)}.{{0,48}}(?:years?|required|must|proficient|expertise)|"
                rf"(?:years?|required|must|proficient|expertise).{{0,48}}{re.escape(skill)})",
                jd,
                re.I,
            )
            if attached:
                return 0
            if not any(phrase_in(jd, item) for item in core):
                return 0
        if core and not any(phrase_in(haystack, skill) for skill in core):
            return 0
        if title_score == 0 and not ic_hint:
            return 0
    elif title_score == 0 and not ic_hint:
        return 0

    score = title_score or (2 if ic_hint else 0)
    city = (cfg.get("user.city") or "").lower()
    country = (cfg.get("user.country") or "").lower()
    if city and city in loc:
        score += 3
    for market in target_markets(cfg):
        if market.lower() in loc:
            score += 2
            break
    if country and country in loc:
        score += 1
    if "remote" in blob:
        score += 1
    if jd:
        for skill in preferred_skills(cfg)[:8]:
            if phrase_in(haystack, skill):
                score += 1
    return score


def _level_blocked_in_title(title: str, level: str, targets: list[str]) -> bool:
    if _allowed_by_targets(level, targets):
        return False
    if not phrase_in(title, level):
        return False
    if level == "staff" and (
        phrase_in(title, "member of technical staff") or phrase_in(title, "mts")
    ):
        return False
    return True


def fetch_json(url: str, timeout: int = 20) -> dict | list | None:
    if _json_fetcher is not None:
        return _json_fetcher(url)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Search fetch failed %s: %s", url, exc)
        return None


def search_muse(cfg: Config) -> list[dict]:
    listings = []
    categories = ["Software Engineering", "Data and Analytics"]
    locations: list[str] = []
    city = cfg.get("user.city") or ""
    country = cfg.get("user.country") or ""
    if city and country:
        locations.append(f"{city}, {country}")
    locations.extend(target_markets(cfg) or ([country] if country else []))
    if not locations:
        locations = ["United States"]
    seen: set[tuple[str, str]] = set()
    for loc in locations[:3]:
        for category in categories:
            url = (
                "https://www.themuse.com/api/public/jobs?page=0&descending=true"
                f"&category={quote_plus(category)}&location={quote_plus(loc)}"
            )
            payload = fetch_json(url)
            if not isinstance(payload, dict):
                continue
            for item in payload.get("results") or []:
                company = ((item.get("company") or {}).get("name") or "").strip()
                role = (item.get("name") or "").strip()
                refs = item.get("refs") or {}
                job_url = (refs.get("landing_page") or refs.get("external") or "").strip()
                locs = ", ".join(
                    (place.get("name") or "")
                    for place in (item.get("locations") or [])
                    if place.get("name")
                )
                key = (company.lower(), role.lower())
                if not company or not role or key in seen:
                    continue
                seen.add(key)
                listings.append(
                    {
                        "company": company,
                        "role": role,
                        "url": job_url,
                        "location": locs,
                        "jd": html_to_text(item.get("contents") or ""),
                        "source": "muse",
                    }
                )
    return listings


def search_remotive(cfg: Config) -> list[dict]:
    roles = target_roles(cfg) or ["software engineer"]
    query = quote_plus(roles[0])
    payload = fetch_json(f"https://remotive.com/api/remote-jobs?search={query}&limit=50")
    if not isinstance(payload, dict):
        return []
    listings = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("jobs") or []:
        company = (item.get("company_name") or "").strip()
        role = (item.get("title") or "").strip()
        job_url = (item.get("url") or "").strip()
        key = (company.lower(), role.lower())
        if not company or not role or key in seen:
            continue
        seen.add(key)
        listings.append(
            {
                "company": company,
                "role": role,
                "url": job_url,
                "location": (item.get("candidate_required_location") or "Remote").strip(),
                "jd": html_to_text(item.get("description") or ""),
                "source": "remotive",
            }
        )
    return listings


def search_greenhouse(cfg: Config) -> list[dict]:
    listings = []
    for board in greenhouse_boards(cfg):
        payload = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{quote_plus(board)}/jobs")
        if not isinstance(payload, dict):
            continue
        for item in payload.get("jobs") or []:
            role = (item.get("title") or "").strip()
            loc = ((item.get("location") or {}).get("name") or "").strip()
            job_url = (item.get("absolute_url") or "").strip()
            if not role:
                continue
            listings.append(
                {
                    "company": board.replace("-", " ").title(),
                    "role": role,
                    "url": job_url,
                    "location": loc,
                    "jd": "",
                    "source": f"greenhouse:{board}",
                    "greenhouse_id": item.get("id"),
                    "greenhouse_board": board,
                }
            )
    return listings


def fill_greenhouse_jd(listing: dict) -> dict:
    board = listing.get("greenhouse_board")
    job_id = listing.get("greenhouse_id")
    if not board or not job_id or listing.get("jd"):
        return listing
    payload = fetch_json(
        f"https://boards-api.greenhouse.io/v1/boards/{quote_plus(str(board))}/jobs/{job_id}"
    )
    if isinstance(payload, dict):
        listing["jd"] = html_to_text(payload.get("content") or "")
        company = ((payload.get("company") or {}).get("name") or "").strip()
        if company:
            listing["company"] = company
    return listing


def folder_key(name: str) -> str:
    """Company-role prefix of applications/<company>-<role>-<YYYY-MM-DD>."""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name).lower()


def listing_key(listing: dict) -> str:
    return f"{slug(listing.get('company') or '')}-{slug(listing.get('role') or '')}".lower()


def _norm_job_url(url: str) -> str:
    raw = (url or "").strip().split("#")[0]
    if not raw:
        return ""
    return raw.rstrip("/").lower()


def existing_job_urls(cfg: Config) -> set[str]:
    urls: set[str] = set()
    apps = cfg.applications_dir
    if not apps.is_dir():
        return urls
    for folder in apps.iterdir():
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.startswith("_"):
            continue
        path = folder / "job.json"
        if not path.exists():
            continue
        try:
            meta = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(meta, dict):
            norm = _norm_job_url(str(meta.get("url") or ""))
            if norm:
                urls.add(norm)
    return urls


def find_existing_package(cfg: Config, job: dict) -> Path | None:
    """Return the application folder if this company/role or job URL was already tailored."""
    key = listing_key(job)
    url = _norm_job_url(job.get("url") or "")
    apps = cfg.applications_dir
    if not apps.is_dir():
        return None
    key_hit = None
    url_hit = None
    for folder in apps.iterdir():
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.startswith("_"):
            continue
        if key and key != "-" and folder_key(folder.name) == key:
            key_hit = folder
        path = folder / "job.json"
        if url and path.exists():
            try:
                meta = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                meta = {}
            if isinstance(meta, dict) and _norm_job_url(str(meta.get("url") or "")) == url:
                url_hit = folder
    return url_hit or key_hit


def existing_keys(cfg: Config) -> set[str]:
    keys: set[str] = set()
    apps = cfg.applications_dir
    if apps.is_dir():
        for folder in apps.iterdir():
            if folder.is_dir() and not folder.name.startswith(".") and not folder.name.startswith("_"):
                keys.add(folder_key(folder.name))
    path = cfg.jobs_path
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        for entry in data.get("jobs") or []:
            if isinstance(entry, dict):
                keys.add(listing_key(entry))
    return {key for key in keys if key and key != "-"}


def ensure_jd(listing: dict) -> dict:
    if (listing.get("jd") or "").strip():
        return listing
    if listing.get("greenhouse_id"):
        listing = fill_greenhouse_jd(listing)
    url = listing.get("url") or ""
    if not (listing.get("jd") or "").strip() and url and not blocked_url(url):
        fetcher = _jd_fetcher or fetch_jd
        listing["jd"] = fetcher(url) or ""
    return listing


def harvest_api_listings(cfg: Config) -> list[dict]:
    listings: list[dict] = []
    for searcher in (search_greenhouse, search_muse, search_remotive):
        try:
            listings.extend(searcher(cfg))
        except Exception as exc:
            log.warning("Search source failed: %s", exc)
    return listings


def saved_jobs_max(cfg: Config, limit: int | None = None) -> int | None:
    raw = cfg.get("hunt.saved_jobs.max")
    if raw is None:
        return hunt_limit(cfg, limit)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return hunt_limit(cfg, limit)
    return n if n > 0 else None


class HuntMatcher:
    """Apply fit gates one posting at a time so hunt can tailor while search continues."""

    def __init__(self, cfg: Config, limit: int | None = None):
        self.cfg = cfg
        self.cap = hunt_limit(cfg, limit)
        self.saved_cap = saved_jobs_max(cfg, limit)
        self.extra_saved = bool(cfg.get("hunt.saved_jobs.extra", False))
        self.have = existing_keys(cfg)
        self.have_urls = existing_job_urls(cfg)
        self.seen_keys: set[str] = set()
        self.chosen: list[dict] = []
        self.saved_kept = 0
        self._cap_logged = False

    def offer(self, listing: dict) -> dict | None:
        key = listing_key(listing)
        url = _norm_job_url(listing.get("url") or "")
        if not key or key in self.seen_keys or key in self.have:
            return None
        if url and url in self.have_urls:
            return None
        item = dict(listing)
        item["fit"] = score_listing(item, self.cfg)
        if item["fit"] <= 0:
            return None
        saved = bool(item.get("saved"))
        if saved:
            if self.saved_cap is not None and self.saved_kept >= self.saved_cap:
                return None
            if not self.extra_saved and self.cap is not None and len(self.chosen) >= self.cap:
                return None
        elif self.cap is not None and len(self.chosen) >= self.cap:
            if not self._cap_logged:
                log.info("Hunt cap %s reached; later matches are not tailored this run.", self.cap)
                self._cap_logged = True
            return None
        item = ensure_jd(item)
        if not (item.get("jd") or "").strip():
            log.info(
                "Skipping %s — %s (no JD text, will not invent)",
                item.get("company"),
                item.get("role"),
            )
            return None
        item["fit"] = score_listing(item, self.cfg)
        if item["fit"] <= 0:
            log.info(
                "Skipping %s — %s (does not match hunt years/skills/level in config)",
                item.get("company"),
                item.get("role"),
            )
            return None
        self.seen_keys.add(key)
        if saved:
            log.info("Saved job counts as a match: %s — %s", item.get("company"), item.get("role"))
            self.saved_kept += 1
        self.chosen.append(item)
        return item


def rank_and_select(cfg: Config, listings: list[dict], limit: int | None = None) -> list[dict]:
    """Keep every posting that passes fit gates. Score is keep/drop, not a top-N rank."""
    matcher = HuntMatcher(cfg, limit)
    saved_hits: list[dict] = []
    search_hits: list[dict] = []
    for listing in listings:
        if listing.get("saved"):
            saved_hits.append(listing)
        else:
            search_hits.append(listing)
    for listing in saved_hits:
        matcher.offer(listing)
    for listing in search_hits:
        matcher.offer(listing)
    return matcher.chosen


def search_jobs(
    cfg: Config,
    *,
    limit: int | None = None,
    fetcher: JsonFetcher | None = None,
    jd_fetcher: JdFetcher | None = None,
) -> list[dict]:
    """API-only hunt. Tests inject fetchers here. Production hunt uses search_jobs_async."""
    global _json_fetcher, _jd_fetcher
    prev_json, prev_jd = _json_fetcher, _jd_fetcher
    if fetcher is not None:
        _json_fetcher = fetcher
    if jd_fetcher is not None:
        _jd_fetcher = jd_fetcher
    try:
        return rank_and_select(cfg, harvest_api_listings(cfg), limit)
    finally:
        _json_fetcher = prev_json
        _jd_fetcher = prev_jd


def _notify_stage(on_stage, line: str) -> None:
    if not on_stage or not (line or "").strip():
        return
    try:
        on_stage(line.strip())
    except Exception:
        log.debug("on_stage failed", exc_info=True)


def _is_stopped(should_stop) -> bool:
    try:
        return bool(should_stop and should_stop())
    except Exception:
        return False


async def search_jobs_async(
    cfg: Config, *, limit: int | None = None, on_listing=None, on_stage=None, should_stop=None
) -> list[dict]:
    """MCP (optional), Camoufox boards, and public APIs. Fit gates run as each posting arrives."""
    from pipeline.browser_hunt import api_sources_enabled, browse_jobs, browser_enabled
    from pipeline.mcp_hunt import harvest_mcp_listings, mcp_indeed_enabled

    matcher = HuntMatcher(cfg, limit)

    def notify(item: dict) -> None:
        kept = matcher.offer(item)
        if not kept:
            return
        if on_listing:
            try:
                on_listing(kept)
            except Exception:
                log.debug("on_listing failed", exc_info=True)

    if _is_stopped(should_stop):
        return list(matcher.chosen)
    if mcp_indeed_enabled(cfg):
        _notify_stage(on_stage, "Searching Indeed")
        for item in await harvest_mcp_listings(cfg):
            if _is_stopped(should_stop):
                return list(matcher.chosen)
            notify(item)
        if _is_stopped(should_stop):
            return list(matcher.chosen)
    if browser_enabled(cfg):
        await browse_jobs(
            cfg, on_listing=notify, on_stage=on_stage, should_stop=should_stop
        )
        if _is_stopped(should_stop):
            return list(matcher.chosen)
    if api_sources_enabled(cfg):
        _notify_stage(on_stage, "Checking public job APIs")
        for item in harvest_api_listings(cfg):
            if _is_stopped(should_stop):
                return list(matcher.chosen)
            notify(item)
    if not _is_stopped(should_stop):
        _notify_stage(on_stage, "Search finished")
    return list(matcher.chosen)
