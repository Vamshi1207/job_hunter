"""Load config.yaml and resolve workspace paths (host or Docker)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

_CACHE = None


def detect_root() -> Path:
    for key in ("JOB_SEARCH_ROOT", "WORKSPACE"):
        env = os.environ.get(key)
        if env:
            path = Path(env).expanduser().resolve()
            if path.is_dir():
                return path

    repo = Path(__file__).resolve().parent.parent
    if (repo / "config.yaml").exists() or (repo / "config.example.yaml").exists():
        return repo

    app = Path("/app")
    if app.is_dir() and (
        (app / "config.yaml").exists() or (app / "config.example.yaml").exists()
    ):
        return app

    return Path.cwd()


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    return data


def load_config(force: bool = False):
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    root = detect_root()
    example = _read_yaml(root / "config.example.yaml")
    overlay = _read_yaml(root / "config.yaml")
    merged = _deep_merge(example, overlay)
    _CACHE = Config(merged, root)
    return _CACHE


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict, root: Path):
        self.data = data
        self.root = root

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, dotted: str, default_rel: str) -> Path:
        rel = self.get(dotted, default_rel) or default_rel
        path = Path(rel).expanduser()
        if path.is_absolute():
            # Host absolute paths from config.yaml are wrong inside Docker.
            if path.exists():
                return path
            return self.root / path.name
        return self.root / path

    @property
    def full_name(self) -> str:
        return self.get("user.full_name", "Candidate")

    @property
    def preferred_name(self) -> str:
        return self.get("user.preferred_name") or self.full_name.split()[0]

    @property
    def last_name(self) -> str:
        parts = self.full_name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    @property
    def cv_pages(self) -> int:
        """Target PDF page count from config.yaml. Minimum 1; any integer is allowed."""
        raw = self.get("cv_format.pages", 2)
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            pages = 2
        return max(1, pages)

    @property
    def cv_stem(self) -> str:
        return f"{self.full_name.replace(' ', '_')}_CV"

    @property
    def applications_dir(self) -> Path:
        return self.path("workspace.applications", "applications")

    @property
    def tracker_path(self) -> Path:
        return self.path("workspace.tracker", "applications/_tracker.md")

    @property
    def master_cv_path(self) -> Path:
        return self.path("workspace.master_cv", "cv_master.md")

    @property
    def html_template_path(self) -> Path:
        return self.path("pipeline.html_template", "resumes/template.html")

    @property
    def jobs_path(self) -> Path:
        return self.path("pipeline.jobs_file", "jobs.yaml")

    @property
    def applied_jobs_path(self) -> Path:
        return self.path("pipeline.applied_file", "applied.yaml")

    @property
    def deleted_jobs_path(self) -> Path:
        return self.path("pipeline.deleted_file", "deleted.yaml")

    @property
    def experience_bank_dir(self) -> Path:
        return self.path("workspace.experience_bank", "experience-bank/")

    @property
    def templates_dir(self) -> Path:
        return self.path("workspace.templates", "templates/")
