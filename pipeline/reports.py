"""Scan application folders for the desk UI."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from pipeline.config import Config

log = logging.getLogger(__name__)

TEXT_FILES = {
    "analysis.md": "analysis",
    "cover_letter.md": "cover_letter",
    "linkedin_dm.txt": "linkedin_dm",
    "why_i_fit.txt": "why_i_fit",
    "playbook.md": "playbook",
}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "applied"}


def _applied_from_tracker(
    cfg: Config, folder_name: str, company: str, role: str, applied_at: str
) -> tuple[bool, str]:
    path = cfg.tracker_path
    if not path.exists():
        return False, applied_at
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return False, applied_at
    company_key = (company or "").strip().lower()
    role_key = (role or "").strip().lower()
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 5:
            continue
        status = parts[4].lower()
        if "draft" in status:
            continue
        if "submitted" not in status and "applied" not in status:
            continue
        folder_cell = Path(parts[5]).name if len(parts) > 5 else ""
        if folder_name and folder_cell == folder_name:
            return True, applied_at or (parts[0] if parts else "")
        if company_key and role_key and parts[1].lower() == company_key and parts[2].lower() == role_key:
            return True, applied_at or (parts[0] if parts else "")
    return False, applied_at


def parse_evaluation(changes_md: str) -> dict:
    score = _int_field(changes_md, r"\*\*Score:\*\*\s*(\d+)")
    honesty = _int_field(changes_md, r"\*\*Honesty:\*\*\s*(\d+|n/a)")
    coverage = _int_field(changes_md, r"\*\*Keyword coverage:\*\*\s*(\d+|n/a)")
    critique_match = re.search(r"\*\*Critique:\*\*\s*(.+)", changes_md)
    critique = critique_match.group(1).strip() if critique_match else ""
    gaps = re.findall(r"^\- (.+)$", changes_md, re.M)
    history_match = re.search(r"\*\*Retry history:\*\*\s*```(.*?)```", changes_md, re.S)
    return {
        "score": score,
        "honesty": honesty,
        "keyword_coverage": coverage,
        "critique": critique,
        "gaps": gaps,
        "retry_history": (history_match.group(1).strip() if history_match else ""),
    }


def _int_field(text: str, pattern: str):
    match = re.search(pattern, text)
    if not match:
        return None
    raw = match.group(1)
    if raw == "n/a":
        return None
    return int(raw)


def _parse_folder_name(name: str) -> tuple[str, str, str]:
    """Best-effort split of {company}-{role}-{YYYY-MM-DD}."""
    date = ""
    rest = name
    date_match = re.search(r"-(\d{4}-\d{2}-\d{2})$", name)
    if date_match:
        date = date_match.group(1)
        rest = name[: date_match.start()]
    parts = rest.split("-", 1)
    company = parts[0].replace("_", " ") if parts else rest
    role = parts[1].replace("-", " ") if len(parts) > 1 else ""
    return company, role, date


def as_host_path(cfg: Config, path: Path | None) -> str:
    """Path on the machine running the browser (host), even when the app is in Docker."""
    if path is None:
        return ""
    resolved = path.resolve()
    host_root = (os.environ.get("HOST_PROJECT_ROOT") or "").strip()
    if host_root:
        try:
            rel = resolved.relative_to(cfg.root.resolve())
            return str(Path(host_root) / rel)
        except ValueError:
            pass
    return str(resolved)


def list_packages(cfg: Config) -> list[dict]:
    try:
        from pipeline.jobs import sync_applied_jobs

        sync_applied_jobs(cfg)
    except Exception as exc:
        log.warning("Could not split applied jobs out of jobs.yaml: %s", exc)
    root = cfg.applications_dir
    if not root.exists():
        return []
    packages = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.startswith("_"):
            continue
        try:
            packages.append(package_summary(cfg, folder))
        except Exception:
            log.exception("Skipping unreadable package %s", folder.name)
            continue
    return packages


def package_summary(cfg: Config, folder: Path) -> dict:
    company, role, date = _parse_folder_name(folder.name)
    pdf = next(iter(sorted(folder.glob("*_CV.pdf"))), None)
    html = next(iter(sorted(folder.glob("*_CV.html"))), None)
    changes = next(iter(sorted(folder.glob("*_changes.md"))), None)
    eval_data = {}
    eval_json = folder / "evaluation.json"
    if eval_json.exists():
        try:
            loaded = json.loads(eval_json.read_text())
            if isinstance(loaded, dict):
                eval_data = loaded
        except json.JSONDecodeError:
            eval_data = {}
    job_meta = _job_meta(folder)
    if job_meta.get("company"):
        company = job_meta["company"]
    if job_meta.get("role"):
        role = job_meta["role"]
    from pipeline.jobs import is_placeholder_company

    if is_placeholder_company(company):
        analysis = folder / "analysis.md"
        if analysis.exists():
            named = re.search(r"\*\*Company:\*\*\s*(.+)", analysis.read_text())
            if named and not is_placeholder_company(named.group(1)):
                company = named.group(1).strip()
    url = (job_meta.get("url") or "").strip()
    apply_url = (job_meta.get("apply_url") or "").strip()
    apply_kind = (job_meta.get("apply_kind") or "").strip()
    from pipeline.jobs import display_location, infer_work_mode

    location = (job_meta.get("location") or "").strip()
    work_mode = (job_meta.get("work_mode") or "").strip().lower() or infer_work_mode(location)
    applied = _truthy(job_meta.get("applied"))
    applied_at = str(job_meta.get("applied_at") or "").strip()
    if not applied:
        applied, applied_at = _applied_from_tracker(cfg, folder.name, company, role, applied_at)
    if not eval_data.get("score"):
        changes = next(iter(sorted(folder.glob("*_changes.md"))), None)
        if changes and changes.exists():
            parsed = parse_evaluation(changes.read_text())
            eval_data = {**parsed, **{k: v for k, v in eval_data.items() if v not in (None, "", [])}}
    from pipeline.cv_export import list_package_exports

    exports = list_package_exports(folder)
    return {
        "id": folder.name,
        "company": company,
        "role": role,
        "url": url,
        "apply_url": apply_url,
        "apply_kind": apply_kind,
        "location": display_location(location, work_mode) or location,
        "work_mode": work_mode,
        "source": job_meta.get("source") or "",
        "date": date,
        "modified": folder.stat().st_mtime,
        "has_pdf": bool(pdf),
        "pdf_name": pdf.name if pdf else None,
        "pdf_path": as_host_path(cfg, pdf) if pdf else "",
        "html_name": exports.get("html_name") or (html.name if html else None),
        "html_path": as_host_path(cfg, html) if html else "",
        "docx_name": exports.get("docx_name"),
        "pages_name": exports.get("pages_name"),
        "score": eval_data.get("score"),
        "ats_score": eval_data.get("score"),
        "honesty": eval_data.get("honesty"),
        "applied": applied,
        "applied_at": applied_at,
        "critique": eval_data.get("critique") or "",
        "evaluation": {
            "score": eval_data.get("score"),
            "honesty": eval_data.get("honesty"),
            "keyword_coverage": eval_data.get("keyword_coverage"),
            "critique": eval_data.get("critique") or "",
            "gaps": eval_data.get("gaps") or [],
            "retry_history": eval_data.get("retry_history") or "",
        },
    }


def package_detail(cfg: Config, folder_id: str) -> dict | None:
    folder = _safe_folder(cfg, folder_id)
    if folder is None:
        return None
    summary = package_summary(cfg, folder)
    texts = {}
    for filename, key in TEXT_FILES.items():
        path = folder / filename
        texts[key] = path.read_text() if path.exists() else ""
    changes = next(iter(sorted(folder.glob("*_changes.md"))), None)
    changes_text = changes.read_text() if changes else ""
    from_md = parse_evaluation(changes_text) if changes_text else {}
    summary["evaluation"] = {**from_md, **(summary.get("evaluation") or {})}
    summary["files"] = texts
    summary["changes"] = changes_text
    return summary


def update_job_meta(folder: Path, **fields) -> dict:
    """Merge keys into job.json (apply_url, etc.)."""
    meta = _job_meta(folder)
    changed = False
    for key, value in fields.items():
        if value is None:
            continue
        text = value.strip() if isinstance(value, str) else value
        if meta.get(key) != text:
            meta[key] = text
            changed = True
    if changed:
        (folder / "job.json").write_text(json.dumps(meta, indent=2))
    return meta


def _job_meta(folder: Path) -> dict:
    path = folder / "job.json"
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
    playbook = folder / "playbook.md"
    if playbook.exists():
        match = re.search(r"JD URL:\s*(https?://\S+)", playbook.read_text())
        if match:
            return {"url": match.group(1).strip()}
    return {}


def package_dir(cfg: Config, folder_id: str) -> Path | None:
    return _safe_folder(cfg, folder_id)


def _safe_folder(cfg: Config, folder_id: str) -> Path | None:
    if not folder_id or folder_id.startswith(".") or folder_id.startswith("_") or "/" in folder_id or "\\" in folder_id:
        return None
    folder = (cfg.applications_dir / folder_id).resolve()
    root = cfg.applications_dir.resolve()
    if root not in folder.parents and folder != root:
        return None
    if not folder.is_dir():
        return None
    return folder


def delete_package_dir(cfg: Config, folder_id: str) -> bool:
    """Remove one applications/<id>/ folder. Never touches files outside applications/."""
    import shutil

    folder = _safe_folder(cfg, folder_id)
    if folder is None:
        return False
    shutil.rmtree(folder)
    return True


def package_file(cfg: Config, folder_id: str, filename: str) -> Path | None:
    folder = _safe_folder(cfg, folder_id)
    if folder is None:
        return None
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    path = (folder / filename).resolve()
    if folder not in path.parents and path != folder:
        return None
    allowed = path.suffix.lower() in {".pdf", ".html", ".md", ".txt", ".docx", ".pages"}
    if not allowed or not path.is_file():
        return None
    return path
