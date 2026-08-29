"""Load the job queue from jobs.yaml. Do not pretend to scrape LinkedIn."""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from pipeline.config import Config

log = logging.getLogger(__name__)

BLOCKED_HOSTS = ("linkedin.com", "www.linkedin.com")


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-")
    return cleaned or "job"


def fetch_jd(url: str, timeout: int = 15) -> Optional[str]:
    """Best-effort fetch for public ATS pages. LinkedIn/Workday usually fail."""
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
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


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
        if not company or company.lower() in {"www", "jobs", "linkedin"}:
            company = guessed

    if not role and first and " at " not in first.lower():
        role = first[:80]

    return company, role


def append_job(cfg: Config, job: dict) -> None:
    """Add a job to jobs.yaml so CLI and UI share the same queue."""
    import yaml

    path = cfg.jobs_path
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    jobs = list(data.get("jobs") or [])
    jobs.append(
        {
            "company": job["company"],
            "role": job["role"],
            "location": job.get("location") or "",
            "url": job.get("url") or "",
            "jd": job["jd"],
        }
    )
    data["jobs"] = jobs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000))
