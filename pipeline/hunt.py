"""Search public boards for roles that fit the profile, then tailor each one."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from pipeline.config import Config
from pipeline.jobs import append_job
from pipeline.search import hunt_limit, search_jobs_async

log = logging.getLogger(__name__)

OnEvent = Callable[[dict], None]
_ROW_FIELDS = frozenset(
    {"package_id", "detail", "location", "work_mode", "ats_score", "apply_url", "apply_kind"}
)


def row_key(listing: dict) -> tuple[str, str, str]:
    return (
        (listing.get("company") or "").strip(),
        (listing.get("role") or "Role").strip(),
        (listing.get("url") or "").strip(),
    )


def stage_needs_browser(line: str) -> bool:
    """True only when hunt is paused for sign-in, 2FA, CAPTCHA, or similar."""
    text = (line or "").strip().lower()
    if not text or "signed in" in text or "sign-in wait ended" in text:
        return False
    return any(
        token in text
        for token in (
            "camoufox panel",
            "camoufox window",
            "extra verification",
            "captcha",
            "2fa",
            "two-factor",
            "checkpoint",
        )
    )


def job_row_event(listing: dict, *, status: str, event_type: str, **extra: Any) -> dict:
    from pipeline.jobs import decorate_listing

    company, role, url = row_key(listing)
    placed = decorate_listing(listing)
    payload = {
        "type": event_type,
        "status": status,
        "company": company,
        "role": role,
        "url": url,
        "location": extra.get("location") or placed.get("location_display") or placed.get("location") or "",
        "work_mode": extra.get("work_mode") or placed.get("work_mode") or "",
        "ats_score": extra["ats_score"] if "ats_score" in extra else listing.get("ats_score"),
        "apply_url": extra.get("apply_url") or listing.get("apply_url") or "",
        "apply_kind": extra.get("apply_kind") or listing.get("apply_kind") or "",
    }
    payload.update(extra)
    if "line" not in payload:
        detail = (extra.get("detail") or "").strip()
        payload["line"] = f"{detail}: {company} — {role}" if detail else f"{status}: {company} — {role}"
    return payload


def progress_snapshot(rows: list[dict]) -> dict:
    ready = working = waiting = skipped = failed = stopped = 0
    working_details: list[str] = []
    for row in rows:
        status = row.get("status") or ""
        if status == "ready":
            ready += 1
        elif status == "working":
            working += 1
            detail = (row.get("detail") or "").strip()
            if detail and detail not in working_details:
                working_details.append(detail)
        elif status in {"queued", "found"}:
            waiting += 1
        elif status == "skipped":
            skipped += 1
        elif status == "failed":
            failed += 1
        elif status == "stopped":
            stopped += 1
    processed = ready + skipped + failed + stopped
    total = len(rows)
    parts = [f"Found {total}", f"{processed} processed"]
    if working_details:
        parts.append(" · ".join(working_details))
    elif working:
        parts.append(f"{working} tailoring")
    if waiting:
        parts.append(f"{waiting} waiting")
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        parts.append(f"{failed} failed")
    if stopped:
        parts.append(f"{stopped} stopped")
    return {
        "type": "progress",
        "found": total,
        "ready": ready,
        "working": working,
        "waiting": waiting,
        "skipped": skipped,
        "failed": failed,
        "stopped": stopped,
        "processed": processed,
        "line": " · ".join(parts),
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
        from pipeline.jobs import decorate_listing

        placed = decorate_listing(listing)
        fields = {k: v for k, v in extra.items() if k in _ROW_FIELDS}
        fields.setdefault("location", placed.get("location_display") or placed.get("location") or "")
        fields.setdefault("work_mode", placed.get("work_mode") or "")
        fields.setdefault("apply_url", listing.get("apply_url") or "")
        fields.setdefault("apply_kind", listing.get("apply_kind") or "")
        if status != "working":
            fields["detail"] = extra.get("detail") or ""
        self._upsert(listing, status, **fields)
        self._emit(job_row_event(listing, status=status, event_type=event_type, **extra))
        self._emit(progress_snapshot(self.rows))

    def stage(self, line: str) -> None:
        text = (line or "").strip()
        if not text:
            return
        self._emit(
            {
                "type": "hunt_stage",
                "line": text,
                "detail": text,
                "browser": stage_needs_browser(text),
            }
        )

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

    def enqueue(self, listing: dict) -> None:
        company, role, _url = row_key(listing)
        if not company and not role:
            return
        existing = next((row for row in self.rows if row_key(row) == row_key(listing)), None)
        if existing and existing.get("status") not in {"found", "queued"}:
            return
        self._push(
            listing,
            status="queued",
            event_type="queued",
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
            queued.append(
                {
                    "company": key[0],
                    "role": key[1],
                    "url": key[2],
                    "status": "queued",
                    "apply_url": listing.get("apply_url") or "",
                    "apply_kind": listing.get("apply_kind") or "",
                    "location": listing.get("location") or "",
                    "work_mode": listing.get("work_mode") or "",
                }
            )
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

    def working(self, listing: dict, detail: str | None = None) -> None:
        company, role, _url = row_key(listing)
        step = (detail or "Writing CV").strip() or "Writing CV"
        existing = next((row for row in self.rows if row_key(row) == row_key(listing)), None)
        first = not existing or existing.get("status") != "working"
        self._push(
            listing,
            status="working",
            event_type="processing",
            detail=step,
            line=f"{step}: {company} — {role}" if first else "",
        )

    def ready(self, listing: dict, package_id: str, *, skipped: bool = False, ats_score=None) -> None:
        company, role, _url = row_key(listing)
        status = "skipped" if skipped else "ready"
        line = (
            f"Already processed {company} — {role}. Skipping."
            if skipped
            else f"Ready: {company} — {role}"
        )
        extra = {"package_id": package_id, "line": line}
        if ats_score is not None:
            extra["ats_score"] = ats_score
        self._push(
            listing,
            status=status,
            event_type="package",
            **extra,
        )

    def failed(self, listing: dict, line: str) -> None:
        self._push(listing, status="failed", event_type="failed", line=line)

    def stopped(self, listing: dict) -> None:
        company, role, _url = row_key(listing)
        self._push(
            listing,
            status="stopped",
            event_type="stopped",
            line=f"Stopped: {company} — {role}",
            detail="",
        )

    def stop_unfinished(self) -> None:
        for row in list(self.rows):
            if row.get("status") in {"found", "queued", "working"}:
                self.stopped(row)


def _is_stopped(should_stop) -> bool:
    try:
        return bool(should_stop and should_stop())
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


def _job_from_listing(listing: dict) -> dict:
    from pipeline.jobs import decorate_listing

    placed = decorate_listing(listing)
    return {
        "company": listing["company"],
        "role": listing["role"],
        "url": listing.get("url") or "",
        "location": placed.get("location") or "",
        "work_mode": placed.get("work_mode") or "",
        "jd": listing["jd"],
        "channel": "saved" if listing.get("saved") else "hunt",
        "source": listing.get("source") or ("saved" if listing.get("saved") else "hunt"),
        "apply_url": listing.get("apply_url") or "",
        "apply_kind": listing.get("apply_kind") or "",
    }


def _ats_score(folder) -> int | None:
    try:
        path = folder / "evaluation.json"
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("score") is not None:
                return int(data["score"])
        changes = next(iter(sorted(folder.glob("*_changes.md"))), None)
    except (TypeError, AttributeError, OSError, json.JSONDecodeError, ValueError):
        return None
    if changes:
        from pipeline.reports import parse_evaluation

        return parse_evaluation(changes.read_text()).get("score")
    return None


async def hunt_and_tailor(
    cfg: Config,
    *,
    fill_form: bool = False,
    max_jobs: int | None = None,
    on_event=None,
    should_stop=None,
) -> list[dict]:
    """Search and tailor in parallel: each match is queued as soon as it passes fit gates."""
    from pipeline.llm import worker_count
    from pipeline.run_pipeline import process_job
    from pipeline.search import find_existing_package

    board = JobProgress(on_event)
    results: list[dict] = []
    work: asyncio.Queue = asyncio.Queue()
    workers_n = worker_count(cfg)
    sem = asyncio.Semaphore(workers_n)

    def empty_result(job: dict) -> dict:
        return {
            "company": job["company"],
            "role": job["role"],
            "url": job["url"],
            "package_id": None,
        }

    async def _tailor(job: dict) -> dict:
        async with sem:
            if _is_stopped(should_stop):
                board.stopped(job)
                return empty_result(job)
            board.working(job, "Writing CV")
            try:
                output_dir = await process_job(
                    job,
                    fill_form=fill_form,
                    on_progress=lambda msg, current=job: board.working(current, msg),
                )
            except asyncio.CancelledError:
                board.stopped(job)
                return empty_result(job)
            except Exception as exc:
                log.exception("Tailor failed for %s — %s", job["company"], job["role"])
                board.failed(job, str(exc))
                return empty_result(job)
            if output_dir:
                try:
                    append_job(cfg, job)
                    from pipeline.jobs import remember_apply_target

                    remember_apply_target(cfg, job)
                except Exception as exc:
                    log.warning("Could not append jobs.yaml: %s", exc)
                board.ready(job, output_dir.name, ats_score=_ats_score(output_dir))
            else:
                board.failed(job, f"No package for {job['company']} — {job['role']}")
            return {
                "company": job["company"],
                "role": job["role"],
                "url": job["url"],
                "package_id": output_dir.name if output_dir else None,
            }

    async def _worker() -> None:
        while True:
            job = await work.get()
            try:
                if job is None:
                    return
                if _is_stopped(should_stop):
                    board.stopped(job)
                    results.append(empty_result(job))
                    continue
                results.append(await _tailor(job))
            except asyncio.CancelledError:
                if job:
                    board.stopped(job)
                raise
            finally:
                work.task_done()

    def submit(listing: dict) -> None:
        if _is_stopped(should_stop):
            board.stopped(listing)
            return
        board.enqueue(listing)
        job = _job_from_listing(listing)
        log.info("Hunt: %s — %s (%s)", job["company"], job["role"], job["url"] or "no url")
        prior = find_existing_package(cfg, job)
        if prior:
            log.info("Already processed %s — %s. Skipping.", job["company"], job["role"])
            board.ready(job, prior.name, skipped=True, ats_score=_ats_score(prior))
            results.append(
                {
                    "company": job["company"],
                    "role": job["role"],
                    "url": job["url"],
                    "package_id": prior.name,
                }
            )
            return
        work.put_nowait(job)

    if _is_stopped(should_stop):
        board.stage("Hunt stopped")
        return []

    worker_tasks = [asyncio.create_task(_worker()) for _ in range(workers_n)]
    cancelled = False
    try:
        log.info("Tailoring matches as they are found (%s worker(s))", workers_n)
        listings = await search_jobs_async(
            cfg,
            limit=max_jobs,
            on_listing=submit,
            on_stage=board.stage if on_event else None,
            should_stop=should_stop,
        )
        if not listings and not board.rows:
            log.warning(
                "Hunt found no matching postings. Check hunt.sources, Camoufox login, and fit filters."
            )
        elif hunt_limit(cfg, max_jobs):
            log.info("Hunt queued %s matching posting(s).", len(listings))
        else:
            log.info("Hunt queued every matching posting (%s).", len(listings))
        if _is_stopped(should_stop):
            board.stage("Hunt stopped")
            while True:
                try:
                    leftover = work.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if leftover:
                    board.stopped(leftover)
                work.task_done()
            board.stop_unfinished()
    except asyncio.CancelledError:
        cancelled = True
        board.stage("Hunt stopped")
        board.stop_unfinished()
        for task in worker_tasks:
            task.cancel()
        raise
    finally:
        if not cancelled:
            for _ in range(workers_n):
                await work.put(None)
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        else:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
    return results
