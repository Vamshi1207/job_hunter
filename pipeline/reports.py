"""Scan application folders for the desk UI."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.config import Config

TEXT_FILES = {
    "analysis.md": "analysis",
    "cover_letter.md": "cover_letter",
    "linkedin_dm.txt": "linkedin_dm",
    "why_i_fit.txt": "why_i_fit",
    "playbook.md": "playbook",
}


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


def list_packages(cfg: Config) -> list[dict]:
    root = cfg.applications_dir
    if not root.exists():
        return []
    packages = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        packages.append(package_summary(cfg, folder))
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
    if not eval_data.get("score"):
        changes = next(iter(sorted(folder.glob("*_changes.md"))), None)
        if changes and changes.exists():
            parsed = parse_evaluation(changes.read_text())
            eval_data = {**parsed, **{k: v for k, v in eval_data.items() if v not in (None, "", [])}}
    return {
        "id": folder.name,
        "company": company,
        "role": role,
        "date": date,
        "modified": folder.stat().st_mtime,
        "has_pdf": bool(pdf),
        "pdf_name": pdf.name if pdf else None,
        "html_name": html.name if html else None,
        "score": eval_data.get("score"),
        "honesty": eval_data.get("honesty"),
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


def _safe_folder(cfg: Config, folder_id: str) -> Path | None:
    if not folder_id or folder_id.startswith(".") or "/" in folder_id or "\\" in folder_id:
        return None
    folder = (cfg.applications_dir / folder_id).resolve()
    root = cfg.applications_dir.resolve()
    if root not in folder.parents and folder != root:
        return None
    if not folder.is_dir():
        return None
    return folder


def package_file(cfg: Config, folder_id: str, filename: str) -> Path | None:
    folder = _safe_folder(cfg, folder_id)
    if folder is None:
        return None
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    path = (folder / filename).resolve()
    if folder not in path.parents and path != folder:
        return None
    allowed = path.suffix.lower() in {".pdf", ".html", ".md", ".txt"}
    if not allowed or not path.is_file():
        return None
    return path
