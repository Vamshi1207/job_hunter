"""Application form fill payload. Never includes a submit action."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import Config
from pipeline.playbook import visa_answers
from pipeline.reports import as_host_path, package_dir


def _text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def fill_fields(cfg: Config, job: dict | None = None) -> dict:
    visa = visa_answers(cfg)
    city = (cfg.get("user.city") or "").strip()
    country = (cfg.get("user.country") or "").strip()
    location = ", ".join(part for part in (city, country) if part)
    company = ((job or {}).get("company") or "").strip()
    return {
        "first_name": cfg.preferred_name,
        "last_name": cfg.last_name,
        "full_name": cfg.full_name,
        "email": (cfg.get("user.email") or "").strip(),
        "phone": str(cfg.get("user.phone") or "").strip(),
        "city": city,
        "country": country,
        "location": location,
        "linkedin": (cfg.get("user.linkedin") or "").strip(),
        "github": (cfg.get("user.github") or "").strip(),
        "website": (cfg.get("user.website") or "").strip(),
        "work_authorization": visa["work_authorization"],
        "sponsorship_now": visa["sponsorship_now"],
        "sponsorship_future": visa["sponsorship_future"],
        "heard_about": f"{company} careers page" if company else "Company careers page",
        "cover_letter": "",
        "why_i_fit": "",
    }


def package_fill_payload(
    cfg: Config,
    *,
    package_id: str = "",
    job: dict | None = None,
    public_base: str = "http://127.0.0.1:8000",
) -> dict:
    """JSON the regular-browser helper uses to fill a form. Never submits."""
    meta = dict(job or {})
    folder: Path | None = None
    if package_id:
        folder = package_dir(cfg, package_id)
        if folder is None:
            raise FileNotFoundError(package_id)
        job_json = folder / "job.json"
        if job_json.exists():
            try:
                loaded = json.loads(job_json.read_text())
                if isinstance(loaded, dict):
                    meta = {**loaded, **{k: v for k, v in meta.items() if v}}
            except json.JSONDecodeError:
                pass
    fields = fill_fields(cfg, meta)
    files: dict = {}
    if folder:
        pdf = next(iter(sorted(folder.glob("*_CV.pdf"))), None)
        cover = folder / "cover_letter.md"
        why = folder / "why_i_fit.txt"
        fields["cover_letter"] = _text(cover)
        fields["why_i_fit"] = _text(why)
        base = public_base.rstrip("/")
        if pdf:
            files["resume"] = {
                "url": f"{base}/api/packages/{package_id}/file/{pdf.name}",
                "name": pdf.name,
                "type": "application/pdf",
                "path": as_host_path(cfg, pdf),
            }
        if cover.exists() and cover.stat().st_size:
            files["cover_letter"] = {
                "url": f"{base}/api/packages/{package_id}/file/{cover.name}",
                "name": cover.name,
                "type": "text/markdown",
                "path": as_host_path(cfg, cover),
            }
    apply_url = (meta.get("apply_url") or "").strip()
    posting = (meta.get("url") or "").strip()
    kind = (meta.get("apply_kind") or "").strip()
    if apply_url:
        from pipeline.apply_url import is_aggregator_url

        if is_aggregator_url(apply_url) and kind != "easy_apply":
            apply_url = ""
    elif kind == "easy_apply":
        apply_url = posting
    return {
        "package_id": package_id,
        "company": (meta.get("company") or "").strip(),
        "role": (meta.get("role") or "").strip(),
        "posting_url": posting,
        "apply_url": apply_url,
        "apply_kind": kind,
        "fields": fields,
        "files": files,
        "never_submit": True,
    }
