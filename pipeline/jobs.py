"""Load the job queue from jobs.yaml."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from pipeline.config import Config

log = logging.getLogger(__name__)

BLOCKED_HOSTS = ("linkedin.com", "www.linkedin.com")
PLACEHOLDER_COMPANIES = {
    "linkedin",
    "linkedin.com",
    "www.linkedin.com",
    "indeed",
    "indeed.com",
    "www.indeed.com",
    "ca.indeed.com",
    "jobs",
    "www",
    "unknown",
}
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
SALARY_OR_DIRECTORY_TITLE = re.compile(
    r"\b(salaries|salary|pay guide|compensation guide|how much does|average base pay|wage guide)\b",
    re.I,
)


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")
    return cleaned or "job"


def fetch_jd(url: str, timeout: int = 15) -> Optional[str]:
    """Best-effort fetch for public ATS pages. LinkedIn is read via Camoufox hunt, not requests."""
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if any(host.endswith(b) or host == b for b in BLOCKED_HOSTS):
        log.warning("Will not scrape %s — paste the JD into jobs.yaml instead.", url)
        return None

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-pipeline/1.0)"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.error("Failed to fetch JD from %s: %s", url, exc)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    return _html_to_text(soup) or None


def _html_to_text(soup: BeautifulSoup) -> str:
    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in clone.get_text(separator="\n").splitlines()]
    return "\n".join(line for line in lines if line)


def parse_job_urls(text: str) -> list[str]:
    """Pull http(s) job links from a paste, one per line or mixed with other text."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip().strip(".,;")
        if not line or line.startswith("#"):
            continue
        matches = URL_IN_TEXT.findall(line)
        if not matches:
            lowered = line.lower()
            if lowered.startswith("www.") or "linkedin.com/" in lowered or "indeed." in lowered:
                matches = [f"https://{line.lstrip('/')}"]
        for url in matches:
            url = url.rstrip(").,]")
            if url in seen:
                continue
            seen.add(url)
            found.append(url)
    return found


def is_directory_or_salary_listing(listing: dict) -> bool:
    """True for salary guides, career-explorer pages, and other non-postings."""
    role = listing.get("role") or ""
    if SALARY_OR_DIRECTORY_TITLE.search(role):
        return True
    url = listing.get("url") or ""
    return bool(url) and not is_job_posting_url(url)


def is_job_posting_url(url: str) -> bool:
    """True only for a single job posting, not search/salary/company directory pages."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return False
    parsed = urlparse(raw.split("#")[0])
    host = parsed.netloc.lower()
    path = parsed.path.lower() or "/"
    if re.search(r"/salar(?:y|ies)(?:/|$)", path):
        return False
    if "indeed." in host:
        if "/career/" in path or "/cmp/" in path or "/forum/" in path:
            return False
        jk = (parse_qs(parsed.query).get("jk") or [None])[0]
        return bool(jk)
    if "linkedin.com" in host:
        if "/jobs/search" in path or "/jobs/collections" in path or "/my-items/" in path:
            return False
        return bool(re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", path))
    if path.rstrip("/") in {"", "/jobs", "/careers", "/career"}:
        return False
    return True


def is_placeholder_company(name: str) -> bool:
    cleaned = re.sub(r"^www\.", "", (name or "").strip().lower())
    if not cleaned:
        return True
    if cleaned in PLACEHOLDER_COMPANIES:
        return True
    host = cleaned.split("/")[0]
    return host.endswith("linkedin.com") or host.endswith("indeed.com") or "indeed." in host


def company_role_from_title(title: str) -> tuple[str, str]:
    """Return (company, role) from a browser/og title."""
    raw = re.sub(r"\s+", " ", (title or "")).strip()
    raw = re.sub(r"\s*[\-|]\s*LinkedIn\s*$", "", raw, flags=re.I)
    raw = re.sub(r"\s*\|\s*LinkedIn\s*$", "", raw, flags=re.I)
    raw = re.sub(r"\s*\|\s*Indeed.*$", "", raw, flags=re.I)
    hiring = re.match(r"(.+?)\s+hiring\s+(.+?)(?:\s+in\s+|\s+\(|$)", raw, re.I)
    if hiring:
        return hiring.group(1).strip(" -–—"), hiring.group(2).strip(" -–—")[:160]
    at_match = re.match(r"(.+?)\s+at\s+(.+)$", raw, re.I)
    if at_match and "|" not in raw:
        return at_match.group(2).strip(" -–—"), at_match.group(1).strip(" -–—")[:160]
    parts = [part.strip(" -–—") for part in re.split(r"\s*\|\s*", raw) if part.strip()]
    if len(parts) >= 2:
        return parts[1], parts[0][:160]
    return "", raw[:160]


def _job_posting_nodes(payload) -> list[dict]:
    nodes: list[dict] = []
    if isinstance(payload, list):
        for item in payload:
            nodes.extend(_job_posting_nodes(item))
        return nodes
    if not isinstance(payload, dict):
        return []
    types = payload.get("@type")
    type_names = types if isinstance(types, list) else [types]
    if any(str(name).lower() == "jobposting" for name in type_names if name):
        nodes.append(payload)
    if isinstance(payload.get("@graph"), list):
        nodes.extend(_job_posting_nodes(payload["@graph"]))
    return nodes


def parse_posting_meta(html: str, url: str = "") -> dict:
    """Company, role, JD, location from ATS/LinkedIn HTML (JSON-LD, og:title, headings)."""
    soup = BeautifulSoup(html or "", "html.parser")
    company = ""
    role = ""
    jd = ""
    location = ""
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").lower()
        if "ld+json" not in script_type:
            continue
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for posting in _job_posting_nodes(payload):
            role = role or str(posting.get("title") or "").strip()
            org = posting.get("hiringOrganization") or posting.get("hiringorganization") or {}
            if isinstance(org, dict):
                company = company or str(org.get("name") or "").strip()
            elif isinstance(org, str):
                company = company or org.strip()
            loc = posting.get("jobLocation") or posting.get("joblocation")
            if isinstance(loc, dict):
                addr = loc.get("address") or {}
                if isinstance(addr, dict):
                    location = location or ", ".join(
                        part for part in (addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")) if part
                    )
                location = location or str(loc.get("name") or "").strip()
            desc = posting.get("description") or ""
            if desc and not jd:
                jd = _html_to_text(BeautifulSoup(str(desc), "html.parser")) if "<" in str(desc) else str(desc).strip()
    og = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "og:title"})
    if og and og.get("content"):
        og_company, og_role = company_role_from_title(og["content"])
        if is_placeholder_company(company):
            company = og_company
        role = role or og_role
    title_el = soup.find("title")
    if title_el:
        t_company, t_role = company_role_from_title(title_el.get_text())
        if is_placeholder_company(company):
            company = t_company
        role = role or t_role
    for selector in (
        "a.topcard__org-name-link",
        ".job-details-jobs-unified-top-card__company-name a",
        ".job-details-jobs-unified-top-card__company-name",
        ".jobs-unified-top-card__company-name",
        "a[href*='/company/']",
        "[data-company-name]",
        ".jobsearch-InlineCompanyRating-companyHeader a",
        "h1",
    ):
        el = soup.select_one(selector)
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if "company" in selector or "org-name" in selector or "href" in selector:
            if is_placeholder_company(company) and not is_placeholder_company(text):
                company = text
        elif selector == "h1":
            role = role or text[:160]
    url_company, _ = infer_company_role(url, "")
    if is_placeholder_company(company) and url_company and not is_placeholder_company(url_company):
        company = url_company
    if is_placeholder_company(company):
        company = ""
    if not jd:
        jd = _html_to_text(soup)
    return {"company": company.strip(), "role": (role or "").strip()[:160], "jd": jd.strip(), "location": location.strip()}


REGION_TOKENS = {
    "qc", "on", "bc", "ab", "mb", "sk", "ns", "nb", "nl", "pe", "yt", "nt", "nu",
    "ca", "us", "usa", "uk", "canada", "united states", "america", "remote",
}


def infer_work_mode(location: str = "", jd: str = "") -> str:
    """hybrid, remote, onsite, or empty if the posting does not say."""
    blob = f"{location or ''}\n{jd or ''}".lower()
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\b(remote|work from home|\bwfh\b|anywhere)\b", blob) and not re.search(
        r"\b(not remote|no remote|not a remote)\b", blob
    ):
        return "remote"
    if re.search(r"\b(on-?site|in-?office|office-based|in the office)\b", blob):
        return "onsite"
    loc = (location or "").strip().lower()
    if loc in {"remote", "anywhere"} or loc.startswith("remote"):
        return "remote"
    if loc:
        return "onsite"
    return ""


def display_location(location: str = "", work_mode: str = "") -> str:
    """Short place list for the desk table, e.g. Montreal, Toronto, Remote."""
    text = (location or "").strip()
    if not text:
        return "Remote" if work_mode == "remote" else ""
    cleaned = re.sub(r"\b(hybrid|on-?site|in-?office|wfh)\b", " ", text, flags=re.I)
    chunks = re.split(r"[\n|/•;·]|(?:\s+-\s+)|(?:\s+or\s+)|(?:\s+and\s+)", cleaned, flags=re.I)
    found: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        piece = re.sub(r"\s+", " ", chunk).strip(" ,-()")
        if not piece:
            continue
        first = piece.split(",")[0].strip()
        key = first.lower()
        if not first or key in REGION_TOKENS or len(first) > 40:
            continue
        if key not in seen:
            seen.add(key)
            found.append(first)
        if len(found) >= 3:
            break
    if work_mode == "remote" and "remote" not in seen and found:
        pass
    if not found:
        if work_mode == "remote" or "remote" in (location or "").lower():
            return "Remote"
        return (location or "").split(",")[0].strip()[:40]
    return ", ".join(found)


def decorate_listing(listing: dict) -> dict:
    item = dict(listing)
    loc = (item.get("location") or "").strip()
    mode = (item.get("work_mode") or "").strip().lower() or infer_work_mode(loc, item.get("jd") or "")
    item["location"] = loc
    item["work_mode"] = mode
    item["location_display"] = display_location(loc, mode)
    from pipeline.apply_url import attach_apply_target

    return attach_apply_target(item)


def fetch_posting(url: str, timeout: int = 20) -> Optional[dict]:
    """Public ATS fetch. LinkedIn returns None so Camoufox can read the signed-in page."""
    host = urlparse(url).netloc.lower()
    if any(host.endswith(blocked) or host == blocked for blocked in BLOCKED_HOSTS):
        return None
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-pipeline/1.0)"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Failed to fetch posting %s: %s", url, exc)
        return None
    meta = parse_posting_meta(response.text, url)
    if not meta.get("jd"):
        return None
    from pipeline.apply_url import extract_apply_from_html

    apply = extract_apply_from_html(response.text, url)
    return {
        "company": meta["company"],
        "role": meta["role"],
        "url": url,
        "location": meta["location"],
        "jd": meta["jd"],
        "source": "fetch",
        "apply_url": apply.apply_url,
        "apply_kind": apply.apply_kind,
    }


def load_jobs(cfg: Config, company_filter: Optional[str] = None) -> list[dict]:
    path = cfg.jobs_path
    if not path.exists():
        example = cfg.root / "jobs.example.yaml"
        hint = f" Copy {example.name} to {path.name} and add job descriptions." if example.exists() else ""
        raise FileNotFoundError(f"Job queue not found: {path}.{hint}")

    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    raw_jobs = data.get("jobs") or []
    jobs = []
    for entry in raw_jobs:
        company = (entry.get("company") or "").strip()
        role = (entry.get("role") or "").strip()
        url = (entry.get("url") or "").strip()
        jd = (entry.get("jd") or entry.get("jd_text") or "").strip()
        if not company or not role:
            log.warning("Skipping job missing company or role: %s", entry)
            continue
        if company_filter and company_filter.lower() not in company.lower():
            continue
        if not jd and url:
            jd = fetch_jd(url) or ""
        if not jd:
            log.error(
                "No JD text for %s — %s. Paste the description under `jd:` in jobs.yaml.",
                company,
                url or "no url",
            )
            continue
        jobs.append(
            {
                "company": company,
                "role": role,
                "url": url,
                "location": (entry.get("location") or "").strip(),
                "jd": jd,
                "folder": f"{slug(company)}-{slug(role)}",
                "apply_url": (entry.get("apply_url") or "").strip(),
                "apply_kind": (entry.get("apply_kind") or "").strip(),
            }
        )
    return jobs


def infer_company_role(url: str = "", jd: str = "") -> tuple[str, str]:
    """Best-effort company and role from a URL and/or JD first line."""
    company = ""
    role = ""
    host_path = re.sub(r"^https?://", "", url or "").split("?")[0]
    patterns = [
        r"(?:boards\.)?greenhouse\.io/([^/]+)",
        r"lever\.co/([^/]+)",
        r"jobs\.ashbyhq\.com/([^/]+)",
        r"ats\.rippling\.com/([^/]+)",
        r"jobs\.workable\.com/([^/]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, host_path, re.I)
        if match:
            company = match.group(1).replace("-", " ").strip().title()
            break

    first = ""
    for line in (jd or "").splitlines():
        if line.strip():
            first = line.strip()
            break
    at_match = re.match(r"(.+?)\s+at\s+(.+?)(?:\s+\(|$)", first, re.I)
    if at_match:
        role = role or at_match.group(1).strip(" -–—")
        guessed = at_match.group(2).strip(" -–—")
        if not company or is_placeholder_company(company):
            company = guessed

    if not role and first and " at " not in first.lower():
        role = first[:80]

    if is_placeholder_company(company):
        company = ""
    return company, role


def apply_pasted_job_text(
    listings: list[dict],
    urls: list[str],
    jd: str,
    *,
    company: str = "",
    role: str = "",
    location: str = "",
) -> list[dict]:
    """Use pasted JD as the tailor source. Fill company/role from URL/JD when missing."""
    text = (jd or "").strip()
    if not text:
        return list(listings)
    url = ""
    if urls:
        url = (urls[0] or "").strip()
    elif listings:
        url = (listings[0].get("url") or "").strip()
    inferred_c, inferred_r = infer_company_role(url, text)
    company = (company or "").strip()
    role = (role or "").strip()
    location = (location or "").strip()
    if listings:
        target = dict(listings[0])
        target["jd"] = text
        target["url"] = (target.get("url") or url or "").strip()
        existing_company = (target.get("company") or "").strip()
        if company:
            target["company"] = company
        elif is_placeholder_company(existing_company) and inferred_c:
            target["company"] = inferred_c
        existing_role = (target.get("role") or "").strip()
        if role:
            target["role"] = role
        elif inferred_r and (not existing_role or existing_role.lower() == "role"):
            target["role"] = inferred_r
        if location:
            target["location"] = location
        return [target, *[dict(item) for item in listings[1:]]]
    return [
        {
            "company": company or inferred_c or "Unknown",
            "role": role or inferred_r or "Role",
            "url": url,
            "location": location,
            "jd": text,
            "source": "desk",
        }
    ]


def listing_has_identity(listing: dict) -> bool:
    company = (listing.get("company") or "").strip()
    role = (listing.get("role") or "").strip()
    return (not is_placeholder_company(company)) and bool(role) and role.lower() != "role"


def append_job(cfg: Config, job: dict) -> None:
    """Add a job to jobs.yaml so CLI and UI share the same queue."""
    import yaml

    path = cfg.jobs_path
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    jobs = list(data.get("jobs") or [])
    row = {
        "company": job["company"],
        "role": job["role"],
        "location": job.get("location") or "",
        "url": job.get("url") or "",
        "jd": job["jd"],
    }
    if job.get("apply_url"):
        row["apply_url"] = job["apply_url"]
    if job.get("apply_kind"):
        row["apply_kind"] = job["apply_kind"]
    jobs.append(row)
    data["jobs"] = jobs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000))
