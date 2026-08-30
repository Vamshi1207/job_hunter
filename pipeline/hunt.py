"""Search public boards for roles that fit the profile, then tailor each one."""

from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.config import Config
from pipeline.jobs import append_job
from pipeline.search import hunt_limit, search_jobs_async

log = logging.getLogger(__name__)

OnEvent = Callable[[dict], None]


def row_key(listing: dict) -> tuple[str, str, str]:
    return (
        (listing.get("company") or "").strip(),
        (listing.get("role") or "Role").strip(),
        (listing.get("url") or "").strip(),
    )


def job_row_event(listing: dict, *, status: str, event_type: str, **extra: Any) -> dict:
    company, role, url = row_key(listing)
    payload = {
        "type": event_type,
        "status": status,
        "company": company,
        "role": role,
        "url": url,
    }
    payload.update(extra)
    if "line" not in payload:
        payload["line"] = f"{status}: {company} — {role}"
    return payload


def progress_snapshot(rows: list[dict]) -> dict:
    ready = working = waiting = skipped = failed = 0
    for row in rows:
        status = row.get("status") or ""
        if status == "ready":
            ready += 1
        elif status == "working":
            working += 1
        elif status in {"queued", "found"}:
            waiting += 1
        elif status == "skipped":
            skipped += 1
        elif status == "failed":
            failed += 1
    processed = ready + skipped + failed
    total = len(rows)
    line = f"Found {total} · {processed} processed · {working} working · {waiting} waiting"
    if skipped:
        line += f" · {skipped} skipped"
    if failed:
        line += f" · {failed} failed"
    return {
        "type": "progress",
        "found": total,
        "ready": ready,
        "working": working,
        "waiting": waiting,
        "skipped": skipped,
        "failed": failed,
        "processed": processed,
        "line": line,
    }


class JobProgress:
    """Emit table-row and count events as listings are found and tailored."""

    def __init__(self, on_event: OnEvent | None = None):
        self.on_event = on_event
        self.rows: list[dict] = []

    def _emit(self, payload: dict) -> None:
        if self.on_event:
            self.on_event(payload)

    def _upsert(self, listing: dict, status: str, **fields: Any) -> dict:
        key = row_key(listing)
        for row in self.rows:
            if row_key(row) == key:
                row["status"] = status
                row.update(fields)
                return row
        row = {
            "company": key[0],
            "role": key[1],
            "url": key[2],
            "status": status,
            **fields,
        }
        self.rows.append(row)
        return row

    def _push(self, listing: dict, *, status: str, event_type: str, **extra: Any) -> None:
        self._upsert(listing, status, **{k: v for k, v in extra.items() if k in {"package_id"}})
        self._emit(job_row_event(listing, status=status, event_type=event_type, **extra))
        self._emit(progress_snapshot(self.rows))

    def found(self, listing: dict) -> None:
        company, role, _url = row_key(listing)
        if not company and not role:
            return
        self._push(
            listing,
            status="found",
            event_type="found",
            line=f"Found {company} — {role}",
        )

    def queue(self, listings: list[dict]) -> None:
        seen: set[tuple[str, str, str]] = set()
        queued: list[dict] = []
        for listing in listings:
            key = row_key(listing)
            if not key[0] and not key[1]:
                continue
            if key in seen:
                continue
            seen.add(key)
            queued.append({"company": key[0], "role": key[1], "url": key[2], "status": "queued"})
        self.rows = queued
        count = len(self.rows)
        self._emit(
            {
                "type": "queue",
                "jobs": [dict(row) for row in self.rows],
                "found": count,
                "line": f"Keeping {count} posting(s) to tailor." if count else "No postings left to tailor.",
            }
        )
        self._emit(progress_snapshot(self.rows))

    def working(self, listing: dict) -> None:
        company, role, _url = row_key(listing)
        self._push(
            listing,
            status="working",
            event_type="processing",
            line=f"Starting tailor for {company} — {role}",
        )

    def ready(self, listing: dict, package_id: str, *, skipped: bool = False) -> None:
        company, role, _url = row_key(listing)
        status = "skipped" if skipped else "ready"
        line = (
            f"Already processed {company} — {role}. Skipping."
            if skipped
            else f"Ready: {company} — {role}"
        )
        self._push(
            listing,
            status=status,
            event_type="package",
            package_id=package_id,
            line=line,
        )

    def failed(self, listing: dict, line: str) -> None:
        self._push(listing, status="failed", event_type="failed", line=line)


async def hunt_and_tailor(
    cfg: Config,
    *,
    fill_form: bool = False,
    max_jobs: int | None = None,
    on_event=None,
) -> list[dict]:
    """Find matching postings and run the same tailor path used by the desk and CLI."""
    from pipeline.run_pipeline import process_job
    from pipeline.search import find_existing_package

    board = JobProgress(on_event)

    def on_listing(item: dict) -> None:
        board.found(item)

    listings = await search_jobs_async(cfg, limit=max_jobs, on_listing=on_listing if on_event else None)
    cap = hunt_limit(cfg, max_jobs)
    if not listings:
        board.queue([])
        log.warning(
            "Hunt found no matching postings (cap %s). Check hunt.sources, Camoufox login, and fit filters.",
            cap,
        )
        return []

    log.info("Hunt selected %s posting(s) to tailor (cap %s).", len(listings), cap)
    board.queue(listings)
    results: list[dict] = []
    for listing in listings:
        job = {
            "company": listing["company"],
            "role": listing["role"],
            "url": listing.get("url") or "",
            "location": listing.get("location") or "",
            "jd": listing["jd"],
            "channel": "saved" if listing.get("saved") else "hunt",
            "source": listing.get("source") or ("saved" if listing.get("saved") else "hunt"),
        }
        log.info("Hunt: %s — %s (%s)", job["company"], job["role"], job["url"] or "no url")
        prior = find_existing_package(cfg, job)
        if prior:
            log.info("Already processed %s — %s. Skipping.", job["company"], job["role"])
            board.ready(job, prior.name, skipped=True)
            results.append(
                {
                    "company": job["company"],
                    "role": job["role"],
                    "url": job["url"],
                    "package_id": prior.name,
                }
            )
            continue
        board.working(job)
        output_dir = await process_job(job, fill_form=fill_form)
        if output_dir:
            try:
                append_job(cfg, job)
            except Exception as exc:
                log.warning("Could not append jobs.yaml: %s", exc)
            board.ready(job, output_dir.name)
        else:
            board.failed(job, f"No package for {job['company']} — {job['role']}")
        results.append(
            {
                "company": job["company"],
                "role": job["role"],
                "url": job["url"],
                "package_id": output_dir.name if output_dir else None,
            }
        )
    return results
