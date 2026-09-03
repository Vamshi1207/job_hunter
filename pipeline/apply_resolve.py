"""Read Apply URLs for already-tailored packages. Does not rewrite CVs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.apply_url import is_aggregator_url, is_resolved_apply
from pipeline.config import Config, load_config
from pipeline.hunt import JobProgress
from pipeline.jobs import remember_apply_target
from pipeline.reports import package_dir, update_job_meta

log = logging.getLogger(__name__)


def _job_from_folder(folder: Path) -> dict | None:
    path = folder / "job.json"
    if not path.exists():
        return None
    try:
        meta = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict):
        return None
    url = (meta.get("url") or meta.get("apply_url") or "").strip()
    if not url.startswith("http"):
        return None
    return {
        "company": (meta.get("company") or folder.name).strip(),
        "role": (meta.get("role") or "").strip(),
        "url": (meta.get("url") or "").strip() or url,
        "apply_url": (meta.get("apply_url") or "").strip(),
        "apply_kind": (meta.get("apply_kind") or "").strip(),
        "location": (meta.get("location") or "").strip(),
        "package_id": folder.name,
        "folder": folder,
    }


def packages_needing_apply_url(cfg: Config, package_ids: list[str] | None = None) -> list[dict]:
    """Packages whose posting is still LinkedIn/Indeed without Easy Apply or a company form."""
    apps = cfg.applications_dir
    if not apps.is_dir():
        return []
    wanted = {item.strip() for item in (package_ids or []) if item and item.strip()}
    jobs: list[dict] = []
    for folder in sorted(apps.iterdir()):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name.startswith("_"):
            continue
        if wanted and folder.name not in wanted:
            continue
        job = _job_from_folder(folder)
        if not job:
            continue
        if is_resolved_apply(job.get("apply_url") or "", job.get("apply_kind") or ""):
            continue
        posting = job.get("url") or job.get("apply_url") or ""
        if not is_aggregator_url(posting) and not is_aggregator_url(job.get("apply_url") or ""):
            continue
        jobs.append(job)
    return jobs


def persist_apply_target(cfg: Config, job: dict) -> None:
    apply_url = (job.get("apply_url") or "").strip()
    apply_kind = (job.get("apply_kind") or "").strip()
    folder = job.get("folder")
    if folder is None and job.get("package_id"):
        folder = package_dir(cfg, str(job.get("package_id") or ""))
    if folder is not None:
        update_job_meta(folder, apply_url=apply_url, apply_kind=apply_kind)
    remember_apply_target(cfg, job)


async def resolve_stored_apply_urls(
    cfg: Config,
    *,
    package_ids: list[str] | None = None,
    on_event=None,
    should_stop=None,
) -> list[dict]:
    """Visit existing LinkedIn/Indeed packages and write apply_url. No CV rewrite."""
    from pipeline.browser_hunt import resolve_apply_jobs_in_browser

    jobs = packages_needing_apply_url(cfg, package_ids)
    board = JobProgress(on_event)
    if not jobs:
        board.stage("No LinkedIn/Indeed packages still need an Apply link")
        return []
    board.stage(f"Reading Apply links for {len(jobs)} already-tailored posting(s). CVs are not rewritten.")
    for job in jobs:
        board.enqueue(job)

    def on_progress(item: dict, target) -> None:
        if target is None:
            board.working(item, "Reading Apply link")
            return
        apply_url = getattr(target, "apply_url", "") or ""
        apply_kind = getattr(target, "apply_kind", "") or ""
        if is_resolved_apply(apply_url, apply_kind):
            item["apply_url"] = apply_url
            item["apply_kind"] = apply_kind
            persist_apply_target(cfg, item)
        board.ready(item, item.get("package_id") or "")
        log.info(
            "Apply link for %s — %s: %s (%s)",
            item.get("company"),
            item.get("role"),
            item.get("apply_url") or apply_url or "(none)",
            item.get("apply_kind") or apply_kind or "unknown",
        )

    results = await resolve_apply_jobs_in_browser(
        cfg,
        jobs,
        on_progress=on_progress,
        on_stage=board.stage,
        should_stop=should_stop,
    )
    resolved = [item for item in results if is_resolved_apply(item.get("apply_url") or "", item.get("apply_kind") or "")]
    board.stage(
        f"Apply links ready for {len(resolved)} of {len(results)} posting(s). Easy Apply opens LinkedIn; others open the company form."
    )
    return results


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Read Apply URLs for existing packages. Does not rewrite CVs."
    )
    parser.add_argument("--package", action="append", dest="packages", help="Limit to this package id (repeatable)")
    args = parser.parse_args()
    cfg = load_config()
    results = asyncio.run(resolve_stored_apply_urls(cfg, package_ids=args.packages))
    log.info("Finished Apply-link pass for %s package(s).", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
