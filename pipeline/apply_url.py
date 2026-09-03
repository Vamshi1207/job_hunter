"""Resolve the actual application form URL from a posting page.

Camoufox never clicks Apply. This module reads hrefs / embedded JSON and,
when needed, follows HTTP redirects. Aggregator pages (LinkedIn, Indeed)
are unwrapped to the company ATS form when that URL is present.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

ATS_HOST_HINTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "workable.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "bamboohr.com",
    "recruitee.com",
    "pinpointhq.com",
    "breezy.hr",
    "teamtailor.com",
    "applytojob.com",
    "eightfold.ai",
    "jobvite.com",
    "rippling.com",
    "oraclecloud.com",
    "ultipro.com",
    "adp.com",
    "paylocity.com",
    "jobappnetwork.com",
    "gem.com",
    "dover.io",
    "polymer.co",
    "comeet.co",
    "freshteam.com",
    "kula.ai",
    "wellfound.com",
    "otta.com",
    "recruitee.com",
)

AGGREGATOR_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "indeed.ca",
    "glassdoor.com",
    "ziprecruiter.com",
    "simplyhired.com",
    "monster.com",
    "naukri.com",
)

JSON_APPLY_KEYS = (
    "companyApplyUrl",
    "companyApplicationUrl",
    "company_apply_url",
    "externalApplyUrl",
    "external_apply_url",
    "applicationUrl",
    "applyStartUrl",
    "applyConnectUrl",
    "applyUrl",
    "apply_url",
    "jobApplyUrl",
)

_URL_IN_JSON = re.compile(
    r'"(?:' + "|".join(JSON_APPLY_KEYS) + r')"\s*:\s*"(https?:[^"]+)"',
    re.I,
)
_VOYAGER_OFFSITE = re.compile(
    r'OffsiteApply"\s*:\s*\{.{0,1500}?"(?:companyApplyUrl|companyApplicationUrl|applyUrl)"\s*:\s*"(https?:[^"]+)"',
    re.I | re.S,
)
_COMMENTED_URL = re.compile(r'<!--\s*"?(https?://[^"<\s]+)"?\s*-->')
_LINKEDIN_ID = re.compile(r"/jobs/view/(?:[^/]*?-)?(\d{6,})")


@dataclass
class ApplyTarget:
    apply_url: str
    apply_kind: str = "unknown"  # ats | easy_apply | company | aggregator
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "apply_url": self.apply_url,
            "apply_kind": self.apply_kind,
        }


def host_of(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", urlparse(url).netloc.lower())
    except Exception:
        return ""


def is_ats_form_url(url: str) -> bool:
    host = host_of(url)
    if not host:
        return False
    return any(host == hint or host.endswith("." + hint) or host.endswith(hint) for hint in ATS_HOST_HINTS)


def is_aggregator_url(url: str) -> bool:
    host = host_of(url)
    return any(host == hint or host.endswith("." + hint) for hint in AGGREGATOR_HOSTS)


def linkedin_job_id(url: str) -> str:
    match = _LINKEDIN_ID.search(url or "")
    return match.group(1) if match else ""


def indeed_jk(url: str) -> str:
    parsed = urlparse((url or "").split("#")[0])
    if "indeed." not in parsed.netloc.lower():
        return ""
    return (parse_qs(parsed.query).get("jk") or [""])[0]


def canonicalize_form_url(url: str) -> str:
    """Normalize known ATS listing URLs to the application form when possible."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return raw
    parsed = urlparse(raw.split("#")[0])
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    query = parsed.query
    if "lever.co" in host:
        path = path.rstrip("/")
        if path and not path.endswith("/apply"):
            path = path + "/apply"
        query = ""
    elif "ashbyhq.com" in host:
        path = path.rstrip("/")
        if path and not path.endswith("/application"):
            # Ashby often hosts the form on the job URL itself; keep as-is.
            pass
    elif "greenhouse.io" in host:
        path = path.rstrip("/") or "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", query, ""))


def classify_url(url: str) -> str:
    if is_ats_form_url(url):
        return "ats"
    if is_aggregator_url(url):
        host = host_of(url)
        if "linkedin.com" in host or "indeed." in host:
            return "aggregator"
        return "aggregator"
    if url.startswith("http"):
        return "company"
    return "unknown"


def display_apply_host(url: str) -> str:
    host = host_of(url)
    if not host:
        return ""
    return host


def apply_label(kind: str) -> str:
    if kind == "easy_apply":
        return "Easy Apply"
    return "Apply"


def _clean_extracted(url: str, base: str = "") -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    raw = raw.replace("\\u0026", "&").replace("\\/", "/")
    raw = unquote(raw)
    if raw.startswith("//"):
        raw = "https:" + raw
    if raw.startswith("/") and base:
        raw = urljoin(base, raw)
    if not raw.startswith("http"):
        return ""
    parsed = urlparse(raw.split("#")[0])
    if parsed.scheme not in {"http", "https"}:
        return ""
    return canonicalize_form_url(raw)


def _walk_json_apply_urls(payload, found: list[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) in JSON_APPLY_KEYS and isinstance(value, str):
                found.append(value)
            else:
                _walk_json_apply_urls(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _walk_json_apply_urls(item, found)


def _urls_from_embedded_json(html: str) -> list[str]:
    found: list[str] = []
    blob = html or ""
    for match in _URL_IN_JSON.finditer(blob):
        found.append(match.group(1))
    for match in _VOYAGER_OFFSITE.finditer(blob):
        found.append(match.group(1))
    for match in _COMMENTED_URL.finditer(blob):
        found.append(match.group(1))
    soup = BeautifulSoup(html or "", "html.parser")
    for script in soup.find_all("script"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or ("http" not in raw and "apply" not in raw.lower()):
            continue
        script_type = str(script.get("type") or "").lower()
        if "ld+json" in script_type or raw.startswith("{") or raw.startswith("["):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            _walk_json_apply_urls(payload, found)
            if isinstance(payload, dict):
                app = payload.get("applicationUrl") or payload.get("url")
                if isinstance(app, str):
                    found.append(app)
    for tag in soup.find_all("code"):
        text = (tag.get_text() or "").strip()
        if text.startswith("http"):
            found.append(text.split()[0].strip('",'))
    return found


def _easy_apply_in_html(html: str) -> bool:
    blob = html or ""
    if re.search(r"apply-button__offsite-apply", blob, re.I):
        return False
    if re.search(r'"easyApply"\s*:\s*true', blob, re.I):
        return True
    if re.search(r'"indeedApplyEnabled"\s*:\s*true', blob, re.I):
        return True
    if re.search(r'"applyMethod"\s*:\s*\{[^}]{0,200}"@type"\s*:\s*"OnsiteApply"', blob, re.I):
        return True
    soup = BeautifulSoup(blob, "html.parser")
    for el in soup.select(
        ".jobs-apply-button, .jobs-apply-button--top-card, "
        "[data-live-test-job-apply-button], .indeed-apply-button, "
        ".jobsearch-IndeedApplyButton"
    ):
        text = f"{el.get('aria-label') or ''} {el.get_text(' ', strip=True)}"
        if re.search(r"easy apply|indeed apply", text, re.I):
            return True
    return False


def extract_apply_from_html(html: str, posting_url: str = "") -> ApplyTarget:
    """Read the form URL from posting HTML. Does not click anything."""
    posting = (posting_url or "").strip()
    candidates: list[str] = []
    for raw in _urls_from_embedded_json(html):
        cleaned = _clean_extracted(raw, posting)
        if cleaned:
            candidates.append(cleaned)

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all("a", href=True):
        label = f"{tag.get('aria-label') or ''} {tag.get_text(' ', strip=True)}"
        href = _clean_extracted(urljoin(posting or "https://example.com", tag["href"]), posting)
        if not href:
            continue
        if re.search(r"\b(apply|easy apply|apply now|apply on company)\b", label, re.I):
            candidates.append(href)
        elif is_ats_form_url(href):
            candidates.append(href)
    for tag in soup.find_all(attrs={"data-indeed-apply-joburl": True}):
        cleaned = _clean_extracted(str(tag.get("data-indeed-apply-joburl") or ""), posting)
        if cleaned:
            candidates.append(cleaned)

    offsite = []
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if is_aggregator_url(url) and is_aggregator_url(posting):
            continue
        offsite.append(url)

    for url in offsite:
        if is_ats_form_url(url):
            return ApplyTarget(canonicalize_form_url(url), "ats", "html")
    for url in offsite:
        if not is_aggregator_url(url):
            return ApplyTarget(url, "company", "html")

    if _easy_apply_in_html(html) and posting:
        return ApplyTarget(posting, "easy_apply", "html")

    if posting and is_ats_form_url(posting):
        return ApplyTarget(canonicalize_form_url(posting), "ats", "posting")
    if posting:
        kind = classify_url(posting)
        if kind == "aggregator" and _easy_apply_in_html(html):
            kind = "easy_apply"
        return ApplyTarget(posting, kind, "posting")
    return ApplyTarget("", "unknown", "")


def is_company_form_url(url: str) -> bool:
    raw = (url or "").strip()
    return bool(raw) and not is_aggregator_url(raw)


def form_url_for_posting(url: str) -> ApplyTarget:
    """Best guess from the posting URL alone (no HTML)."""
    raw = (url or "").strip()
    if not raw:
        return ApplyTarget("", "unknown", "")
    if is_ats_form_url(raw):
        return ApplyTarget(canonicalize_form_url(raw), "ats", "posting")
    return ApplyTarget(raw, classify_url(raw), "posting")


def linkedin_guest_posting_url(url: str) -> str:
    job_id = linkedin_job_id(url)
    if not job_id:
        return ""
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def fetch_html(url: str, timeout: int = 8) -> Optional[str]:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-pipeline/1.0)"},
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        log.info("Apply URL fetch failed for %s: %s", url, exc)
        return None


def follow_apply_redirects(url: str, timeout: int = 8) -> str:
    """HTTP GET the apply href and return the landing URL. Never a browser click."""
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return raw
    if is_ats_form_url(raw) or not is_aggregator_url(raw):
        return canonicalize_form_url(raw)
    try:
        response = requests.get(
            raw,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-pipeline/1.0)"},
            timeout=timeout,
            allow_redirects=True,
        )
        final = response.url or raw
        return canonicalize_form_url(final)
    except requests.RequestException as exc:
        log.info("Apply redirect follow failed for %s: %s", url, exc)
        return raw


def resolve_apply_target(url: str, html: str = "") -> ApplyTarget:
    """Prefer an off-platform form URL; keep Easy Apply when that is the form."""
    posting = (url or "").strip()
    if html:
        target = extract_apply_from_html(html, posting)
        if target.apply_url and target.apply_kind in {"ats", "company"}:
            return target
        if target.apply_kind == "easy_apply":
            return target
    if posting and is_ats_form_url(posting):
        return ApplyTarget(canonicalize_form_url(posting), "ats", "posting")
    if html:
        return extract_apply_from_html(html, posting)
    return form_url_for_posting(posting)


def resolve_apply_from_web(url: str, html: str = "") -> ApplyTarget:
    """Extract from HTML, then fetch LinkedIn guest HTML if still on an aggregator."""
    target = resolve_apply_target(url, html)
    if target.apply_kind in {"ats", "company", "easy_apply"} and target.apply_url:
        if target.apply_kind == "easy_apply" or not is_aggregator_url(target.apply_url):
            return target
    posting = (url or "").strip()
    guest = linkedin_guest_posting_url(posting)
    if guest:
        page_html = fetch_html(guest)
        if page_html:
            found = extract_apply_from_html(page_html, posting or guest)
            if found.apply_url and found.apply_kind in {"ats", "company"}:
                followed = follow_apply_redirects(found.apply_url)
                if followed and not is_aggregator_url(followed):
                    kind = "ats" if is_ats_form_url(followed) else "company"
                    return ApplyTarget(followed, kind, "web")
                return found
            if found.apply_kind == "easy_apply":
                return found
    if target.apply_url:
        if is_aggregator_url(target.apply_url):
            return target
        followed = follow_apply_redirects(target.apply_url)
        if followed and not is_aggregator_url(followed):
            kind = "ats" if is_ats_form_url(followed) else "company"
            return ApplyTarget(followed, kind, "redirect")
        target.apply_url = followed or target.apply_url
        return target
    return form_url_for_posting(posting)


def attach_apply_target(listing: dict, html: str = "") -> dict:
    item = dict(listing)
    existing = (item.get("apply_url") or "").strip()
    existing_kind = (item.get("apply_kind") or "").strip()
    if existing and existing_kind in {"ats", "company", "easy_apply"}:
        if existing_kind != "easy_apply":
            item["apply_url"] = canonicalize_form_url(existing)
        item["apply_kind"] = existing_kind
        return item
    if existing and not is_aggregator_url(existing):
        item["apply_kind"] = existing_kind or classify_url(existing)
        item["apply_url"] = canonicalize_form_url(existing)
        return item
    target = resolve_apply_target(item.get("url") or "", html)
    if target.apply_url:
        item["apply_url"] = target.apply_url
        item["apply_kind"] = target.apply_kind
    return item
