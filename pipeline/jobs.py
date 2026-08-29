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
