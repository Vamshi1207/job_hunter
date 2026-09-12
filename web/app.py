"""Desk UI: submit a job URL, watch the run, read reports."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.config import load_config
from pipeline.jobs import (
    append_job,
    apply_pasted_job_text,
    extract_company_role_from_jd,
    infer_company_role,
    listing_has_identity,
    parse_job_urls,
    remember_apply_target,
)
from pipeline.reports import delete_package_dir, list_packages, package_detail, package_file, package_dir
from pipeline.run_pipeline import process_job
from pipeline.search import hunt_limit, hunt_locations, preferred_city, target_markets, target_roles

WEB_ROOT = Path(__file__).resolve().parent
STATIC = WEB_ROOT / "static"

app = FastAPI(title="Job desk")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

log = logging.getLogger("jobdesk")

_runs: dict[str, dict] = {}
_run_lock = threading.Lock()
_apply_lock = threading.Lock()
_pending_apply: dict | None = None
PENDING_APPLY_TTL = 30 * 60


def _load_cfg():
    try:
        return load_config(force=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _public_base() -> str:
    return (os.environ.get("JOB_DESK_URL") or "http://127.0.0.1:8000").rstrip("/")


def _apply_helper_path(cfg) -> str:
    from pipeline.reports import as_host_path

    return as_host_path(cfg, cfg.root / "extension")


def _remember_pending_apply(payload: dict) -> None:
    global _pending_apply
    with _apply_lock:
        _pending_apply = {"payload": payload, "at": time.time()}


def _new_run(run_id: str, sink: queue.Queue, *, kind: str) -> dict:
    return {
        "id": run_id,
        "kind": kind,
        "status": "running",
        "company": "",
        "role": "",
        "queue": sink,
        "package_id": None,
        "packages": [],
        "error": None,
        "stop": threading.Event(),
        "loop": None,
        "task": None,
        "thread": None,
        "browser": False,
    }


def _stop_requested(run_id: str) -> bool:
    with _run_lock:
        event = (_runs.get(run_id) or {}).get("stop")
    return bool(event is not None and event.is_set())


def _browser_busy() -> bool:
    with _run_lock:
        _reclaim_finished_runs_locked()
        return any(
            run.get("status") in {"running", "stopping"}
            and run.get("kind") in {"hunt", "run", "resolve", "unsave"}
            for run in _runs.values()
        )


_unsave_queue: queue.Queue = queue.Queue()
_unsave_worker_thread: threading.Thread | None = None
_unsave_worker_lock = threading.Lock()


def _enqueue_linkedin_action(cfg, job_url: str, package_id: str = "", action: str = "apply") -> None:
    """Queue a LinkedIn job action ('apply' or 'unsave') via Camoufox in the background."""
    global _unsave_worker_thread
    if not job_url:
        return
    _unsave_queue.put((cfg, job_url, package_id, action))
    with _unsave_worker_lock:
        if _unsave_worker_thread is None or not _unsave_worker_thread.is_alive():
            _unsave_worker_thread = threading.Thread(target=_unsave_worker_loop, daemon=True)
            _unsave_worker_thread.start()


def _enqueue_linkedin_unsave(cfg, job_url: str, package_id: str = "") -> None:
    """Queue a LinkedIn job to be unsaved via Camoufox in the background."""
    _enqueue_linkedin_action(cfg, job_url, package_id=package_id, action="unsave")


def _unsave_worker_loop() -> None:
    """Background worker pulling LinkedIn tasks and executing them sequentially."""
    while True:
        try:
            item = _unsave_queue.get(timeout=2.0)
        except queue.Empty:
            break
        if len(item) == 4:
            cfg, job_url, package_id, action = item
        else:
            cfg, job_url, package_id = item
            action = "apply"

        try:
            # If the package was unchecked before the worker got to it, skip
            if action == "apply" and package_id:
                try:
                    folder = package_dir(cfg, package_id)
                    if folder:
                        from pipeline.reports import _job_meta

                        meta = _job_meta(folder)
                        if meta.get("applied") is False:
                            log.info("Job %s was unapplied before Camoufox apply confirmation; skipping.", package_id)
                            continue
                except Exception:
                    pass

            # Wait if browser is busy with hunt or resolve
            for _ in range(120):
                with _run_lock:
                    _reclaim_finished_runs_locked()
                    hunt_or_resolve_busy = any(
                        run.get("status") in {"running", "stopping"}
                        and run.get("kind") in {"hunt", "run", "resolve"}
                        for run in _runs.values()
                    )
                if not hunt_or_resolve_busy:
                    break
                time.sleep(1.0)

            run_id = uuid.uuid4().hex[:10]
            run = _new_run(run_id, queue.Queue(), kind="unsave")
            with _run_lock:
                _runs[run_id] = run

            try:
                if action == "unsave":
                    from pipeline.browser_hunt import unsave_linkedin_job_in_browser

                    _run_async(lambda: unsave_linkedin_job_in_browser(cfg, job_url))
                else:
                    from pipeline.browser_hunt import mark_linkedin_job_applied_in_browser

                    _run_async(lambda: mark_linkedin_job_applied_in_browser(cfg, job_url))
            except Exception as exc:
                log.warning("Background Camoufox %s error for %s: %s", action, job_url, exc)
            finally:
                with _run_lock:
                    run["status"] = "finished"
        finally:
            _unsave_queue.task_done()


def _run_async(factory):
    """Run a coroutine from a sync FastAPI route, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    result: list = []
    error: list = []

    def worker() -> None:
        try:
            result.append(asyncio.run(factory()))
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _remember_browser(run_id: str, item: dict) -> None:
    if not isinstance(item, dict) or item.get("type") != "hunt_stage":
        return
    with _run_lock:
        run = _runs.get(run_id)
        if run is not None:
            run["browser"] = bool(item.get("browser"))


def _emit_run_event(run_id: str, sink: queue.Queue, item: dict) -> None:
    _remember_browser(run_id, item)
    sink.put(item)


def _bind_run_task(run_id: str) -> None:
    cancel_now = False
    with _run_lock:
        run = _runs.get(run_id)
        if not run:
            return
        run["loop"] = asyncio.get_running_loop()
        run["task"] = asyncio.current_task()
        stop = run.get("stop")
        cancel_now = bool(stop is not None and stop.is_set())
    if cancel_now:
        task = asyncio.current_task()
        if task is not None:
            task.cancel()


def _thread_finished(run: dict) -> bool:
    thread = run.get("thread")
    return bool(thread is not None and thread.ident is not None and not thread.is_alive())


def _reclaim_finished_runs_locked() -> None:
    for run in _runs.values():
        if run.get("status") not in {"running", "stopping"}:
            continue
        if not _thread_finished(run):
            continue
        if run.get("stop") is not None and run["stop"].is_set():
            run["status"] = "stopped"
            run["error"] = run.get("error") or "Stopped"
        elif run.get("status") == "stopping":
            run["status"] = "stopped"
            run["error"] = run.get("error") or "Stopped"
        else:
            run["status"] = "error"
            run["error"] = run.get("error") or "Hunt ended unexpectedly"


def _active_run_locked(*, kind: str | None = None) -> dict | None:
    _reclaim_finished_runs_locked()
    for run in reversed(list(_runs.values())):
        if run.get("status") not in {"running", "stopping"}:
            continue
        if kind and run.get("kind") != kind:
            continue
        return run
    return None


def _active_hunt() -> dict | None:
    with _run_lock:
        return _active_run_locked(kind="hunt")


def _mark_stopped(run_id: str, packages: list[str] | None = None) -> None:
    with _run_lock:
        run = _runs.get(run_id)
        if not run:
            return
        if packages is not None:
            run["packages"] = packages
            run["package_id"] = packages[0] if packages else None
        run["status"] = "stopped"
        run["error"] = "Stopped"


class InspectRequest(BaseModel):
    url: str = ""
    jd: str = ""


class HuntRequest(BaseModel):
    max_jobs: Optional[int] = None


class RunRequest(BaseModel):
    url: str = ""
    urls: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    jd: str = Field(default="", min_length=0)


class LaunchApplyRequest(BaseModel):
    package_id: str = ""
    url: str = ""
    company: str = ""
    role: str = ""


class FormQuestion(BaseModel):
    key: str
    label: str
    kind: str = "text"
    options: list[str] = Field(default_factory=list)


class FormAnswerRequest(BaseModel):
    url: str = ""
    package_id: str = ""
    questions: list[FormQuestion] = Field(default_factory=list)
    feedback: str = ""


class RememberJobRequest(BaseModel):
    company: str = ""
    role: str = ""
    url: str = ""
    location: str = ""
    jd: str = ""
    source: str = ""
    channel: str = ""
    saved: Optional[bool] = None


class MarkAppliedRequest(BaseModel):
    applied: bool = True


class QueueLogHandler(logging.Handler):
    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait({"type": "log", "line": self.format(record)})
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text())


@app.get("/api/me")
def me() -> dict:
    cfg = _load_cfg()
    from pipeline.fabrication import settings_payload

    return {
        "name": cfg.full_name,
        "pages": cfg.cv_pages,
        "city": cfg.get("user.city"),
        "country": cfg.get("user.country"),
        "cv_format": {
            "pages": cfg.cv_pages,
            "header_align": cfg.get("cv_format.header_align", "center"),
            "body_align": cfg.get("cv_format.body_align", "justify"),
            "keep_together": cfg.get("cv_format.keep_together") or ["skills", "education"],
            "section_order": cfg.get("cv_format.section_order"),
            "density": cfg.get("cv_format.density", "compact"),
        },
        "pipeline": settings_payload(cfg),
        "hunt": {
            "max_jobs": hunt_limit(cfg),
            "roles": target_roles(cfg),
            "markets": target_markets(cfg),
            "search_locations": hunt_locations(cfg),
            "preferred_city": preferred_city(cfg),
            "years_experience": cfg.get("hunt.years_experience", cfg.get("career.years_experience")),
            "years_buffer": cfg.get("hunt.years_buffer", 2),
            "exclude_levels": cfg.get("hunt.exclude_levels") or [],
            "reject_skills": cfg.get("hunt.reject_skills") or [],
            "login_wait_seconds": int(cfg.get("hunt.browser.login_wait_seconds", 300) or 300),
        },
        "camoufox": {
            "vnc": os.environ.get(
                "CAMOUFOX_VNC_URL",
                "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale",
            ),
        },
        "apply_helper": {
            "extension_path": _apply_helper_path(cfg),
            "userscript": "/static/fill-helper.user.js",
        },
    }


class SettingsUpdate(BaseModel):
    fabrication_freedom: Optional[object] = None  # "auto" or 0–5
    fabrication_freedom_min: Optional[int] = None
    fabrication_freedom_max: Optional[int] = None
    generate_cover_letter: Optional[bool] = None


@app.get("/api/settings")
def get_settings() -> dict:
    from pipeline.fabrication import settings_payload

    return settings_payload(_load_cfg())


@app.patch("/api/settings")
def patch_settings(body: SettingsUpdate) -> dict:
    from pipeline.fabrication import (
        clamp_level,
        settings_payload,
        update_fabrication_freedom,
        update_fabrication_freedom_min,
        update_fabrication_freedom_max,
        update_generate_cover_letter,
    )
    from pipeline.config import load_config

    cfg = _load_cfg()
    path = cfg.root / "config.yaml"
    changed = False
    if body.fabrication_freedom is not None:
        raw = body.fabrication_freedom
        if isinstance(raw, str) and raw.strip().lower() in ("auto", "automatic"):
            update_fabrication_freedom(path, "auto")
        else:
            update_fabrication_freedom(path, clamp_level(raw))
        changed = True
    if body.fabrication_freedom_min is not None:
        update_fabrication_freedom_min(path, clamp_level(body.fabrication_freedom_min))
        changed = True
    if body.fabrication_freedom_max is not None:
        update_fabrication_freedom_max(path, clamp_level(body.fabrication_freedom_max))
        changed = True
    if body.generate_cover_letter is not None:
        update_generate_cover_letter(path, body.generate_cover_letter)
        changed = True
    if not changed:
        raise HTTPException(
            status_code=400,
            detail="fabrication_freedom, fabrication_freedom_min, fabrication_freedom_max, or generate_cover_letter is required",
        )
    load_config(force=True)
    return settings_payload(load_config(force=True))


@app.post("/api/inspect")
def inspect(body: InspectRequest) -> dict:
    from pipeline.jobs import fetch_jd, fetch_posting

    url = (body.url or "").strip()
    urls = parse_job_urls(url)
    target = urls[0] if urls else url
    jd = (body.jd or "").strip()
    posting = fetch_posting(target) if target else None
    if posting:
        jd = jd or posting.get("jd") or ""
        return {
            "url": target,
            "blocked": False,
            "fetched": True,
            "needs_jd": not bool(jd),
            "jd": jd,
            "company": posting.get("company") or "",
            "role": posting.get("role") or "",
        }
    blocked = bool(target) and "linkedin.com" in target.lower()
    fetched = None
    if target and not blocked and not jd:
        fetched = fetch_jd(target)
        if fetched:
            jd = fetched
    company, role = infer_company_role(target, jd)
    return {
        "url": target,
        "blocked": blocked,
        "fetched": bool(fetched),
        "needs_jd": not bool(jd),
        "jd": jd,
        "company": company,
        "role": role,
    }


@app.get("/api/packages")
def packages() -> dict:
    cfg = _load_cfg()
    return {"packages": list_packages(cfg)}


@app.get("/api/packages/{package_id}")
def package(package_id: str) -> dict:
    cfg = _load_cfg()
    detail = package_detail(cfg, package_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return detail


@app.get("/api/packages/{package_id}/file/{filename}")
def package_download(package_id: str, filename: str):
    cfg = _load_cfg()
    path = package_file(cfg, package_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    suffix = path.suffix.lower()
    media = "text/plain; charset=utf-8"
    disposition = "inline"
    if suffix == ".pdf":
        media = "application/pdf"
    elif suffix == ".html":
        media = "text/html; charset=utf-8"
        disposition = "attachment"
    elif suffix == ".docx":
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        disposition = "attachment"
    elif suffix == ".pages":
        media = "application/vnd.apple.pages"
        disposition = "attachment"
    return FileResponse(path, media_type=media, content_disposition_type=disposition, filename=path.name)


@app.post("/api/packages/{package_id}/rebuild-pdf")
async def rebuild_pdf(package_id: str) -> dict:
    cfg = _load_cfg()
    folder = package_dir(cfg, package_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Package not found")
    from pipeline.tailor import rebuild_package_pdf

    try:
        pdf = await rebuild_package_pdf(folder)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("PDF rebuild failed for %s", package_id)
        raise HTTPException(status_code=500, detail=f"PDF rebuild failed: {exc}") from exc
    return {"ok": True, "id": package_id, "pdf_name": pdf.name}


@app.delete("/api/packages/{package_id}")
def delete_package(package_id: str, keep: bool = False) -> dict:
    cfg = _load_cfg()
    from pipeline.reports import _job_meta
    from pipeline.jobs import is_linkedin_saved_job

    folder = package_dir(cfg, package_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Package not found")
    meta = _job_meta(folder)
    was_applied = bool(meta.get("applied"))
    is_saved = is_linkedin_saved_job(meta, folder, cfg)
    url = (meta.get("url") or "").strip()

    if not delete_package_dir(cfg, package_id):
        raise HTTPException(status_code=404, detail="Package not found")
    try:
        from pipeline.jobs import record_deleted_job

        record_deleted_job(cfg, meta)
    except Exception as exc:
        log.warning("Could not record deleted job in deleted.yaml: %s", exc)
    if not keep:
        try:
            from pipeline.jobs import forget_job

            forget_job(cfg, meta)
        except Exception as exc:
            log.warning("Could not remove deleted job from jobs.yaml: %s", exc)

    unsave_triggered = False
    if not was_applied and is_saved and "linkedin.com" in url.lower():
        log.info("Triggered background Camoufox unsave on delete (not applied) for %s: %s", package_id, url)
        _enqueue_linkedin_action(cfg, url, package_id="", action="unsave")
        unsave_triggered = True

    return {"ok": True, "id": package_id, "keep": keep, "unsave_triggered": unsave_triggered}


class DeleteJobRequest(BaseModel):
    url: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    jd: str = ""


@app.post("/api/jobs/delete")
def delete_job(body: DeleteJobRequest) -> dict:
    """Record a discarded/deleted job in deleted.yaml and remove from jobs.yaml."""
    cfg = _load_cfg()
    from pipeline.jobs import forget_job, record_deleted_job

    job_data = {
        "company": (body.company or "").strip(),
        "role": (body.role or "").strip(),
        "url": (body.url or "").strip(),
        "location": (body.location or "").strip(),
        "jd": (body.jd or "").strip(),
    }
    record_deleted_job(cfg, job_data)
    forget_job(cfg, job_data)
    
    unsave_triggered = False
    url = (body.url or "").strip()
    if "linkedin.com" in url.lower():
        log.info("Triggered background Camoufox unsave on delete_job: %s", url)
        _enqueue_linkedin_action(cfg, url, package_id="", action="unsave")
        unsave_triggered = True
        
    return {"ok": True, "unsave_triggered": unsave_triggered}


@app.post("/api/jobs/remember")
def remember_job(body: RememberJobRequest) -> dict:
    """Keep a posting in jobs.yaml so hunt will not add it again."""
    cfg = _load_cfg()
    if not (body.company or "").strip() and not (body.url or "").strip():
        raise HTTPException(status_code=400, detail="Need a company or URL to remember.")
    job_dict = {
        "company": (body.company or "").strip() or "Unknown",
        "role": (body.role or "").strip() or "Role",
        "url": (body.url or "").strip(),
        "location": (body.location or "").strip(),
        "jd": (body.jd or "").strip(),
    }
    if body.source:
        job_dict["source"] = body.source
    if body.channel:
        job_dict["channel"] = body.channel
    if body.saved is not None:
        job_dict["saved"] = body.saved

    append_job(cfg, job_dict)

    from pipeline.jobs import is_linkedin_saved_job

    unsave_triggered = False
    url = (body.url or "").strip()
    if is_linkedin_saved_job(job_dict, cfg=cfg) and "linkedin.com" in url.lower():
        log.info("Triggered background Camoufox unsave on remember_job: %s", url)
        _enqueue_linkedin_action(cfg, url, package_id="", action="unsave")
        unsave_triggered = True

    return {"ok": True, "unsave_triggered": unsave_triggered}


@app.post("/api/packages/{package_id}/applied")
def mark_package_applied(package_id: str, body: MarkAppliedRequest) -> dict:
    cfg = _load_cfg()
    folder = package_dir(cfg, package_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Package not found")
    from pipeline.reports import package_summary, update_job_meta

    fields = {"applied": body.applied}
    if body.applied:
        fields["applied_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            from pipeline.fill import clear_package_answers_cache

            clear_package_answers_cache(cfg, package_id)
        except Exception as exc:
            log.warning("Could not clear answers cache for %s: %s", package_id, exc)
    else:
        fields["applied_at"] = ""
    meta = update_job_meta(folder, **fields)
    try:
        from pipeline.jobs import set_job_applied

        set_job_applied(
            cfg,
            meta,
            applied=body.applied,
            applied_at=str(fields.get("applied_at") or ""),
        )
    except Exception as exc:
        log.warning("Could not move job between jobs.yaml and applied.yaml: %s", exc)

    summary = package_summary(cfg, folder)
    unsave_triggered = False
    if body.applied:
        try:
            from pipeline.jobs import is_linkedin_saved_job

            if is_linkedin_saved_job(meta, folder=folder, cfg=cfg):
                job_url = (meta.get("url") or "").strip()
                if job_url:
                    _enqueue_linkedin_unsave(cfg, job_url, package_id)
                    unsave_triggered = True
                    log.info("Triggered background Camoufox unsave for %s: %s", package_id, job_url)
        except Exception as exc:
            log.warning("Could not trigger Camoufox unsave for %s: %s", package_id, exc)

    summary["unsave_triggered"] = unsave_triggered
    return summary


@app.get("/api/packages/{package_id}/fill")
def package_fill(package_id: str) -> dict:
    cfg = _load_cfg()
    folder = package_dir(cfg, package_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Package not found")
    from pipeline.fill import package_fill_payload

    return package_fill_payload(cfg, package_id=package_id, public_base=_public_base())


@app.post("/api/apply/launch")
def launch_apply(body: LaunchApplyRequest) -> dict:
    """Resolve the form URL, store a fill payload, and return both. Never submits."""
    cfg = _load_cfg()
    from pipeline.apply_url import is_aggregator_url, is_resolved_apply, resolve_apply_from_web
    from pipeline.fill import package_fill_payload
    from pipeline.reports import _job_meta, update_job_meta

    package_id = (body.package_id or "").strip()
    job: dict = {}
    folder = None
    if package_id:
        folder = package_dir(cfg, package_id)
        if folder is None:
            raise HTTPException(status_code=404, detail="Package not found")
        job = dict(_job_meta(folder))
    if body.company:
        job["company"] = body.company
    if body.role:
        job["role"] = body.role
    posting = (body.url or job.get("url") or "").strip()
    if posting:
        job["url"] = posting
    apply_url = (job.get("apply_url") or "").strip()
    apply_kind = (job.get("apply_kind") or "").strip()
    if not is_resolved_apply(apply_url, apply_kind):
        try:
            target = resolve_apply_from_web(posting or apply_url)
            apply_url = target.apply_url or apply_url
            apply_kind = target.apply_kind or apply_kind
        except Exception as exc:
            log.warning("Could not resolve form URL for %s: %s", posting, exc)
        if (
            posting
            and is_aggregator_url(posting)
            and not is_resolved_apply(apply_url, apply_kind)
            and not _browser_busy()
        ):
            try:
                from pipeline.browser_hunt import resolve_apply_in_browser

                found = _run_async(lambda: resolve_apply_in_browser(cfg, posting))
                if found.apply_url:
                    apply_url = found.apply_url
                    apply_kind = found.apply_kind or apply_kind
            except Exception as exc:
                log.warning("Camoufox could not read the form URL for %s: %s", posting, exc)
        job["apply_url"] = apply_url
        job["apply_kind"] = apply_kind
        if folder and folder.exists():
            update_job_meta(folder, apply_url=apply_url, apply_kind=apply_kind)
        remember_apply_target(cfg, job)
    job["apply_url"] = apply_url
    job["apply_kind"] = apply_kind
    payload = package_fill_payload(
        cfg, package_id=package_id, job=job, public_base=_public_base()
    )
    _remember_pending_apply(payload)
    return payload


def _current_pending_payload() -> dict | None:
    with _apply_lock:
        data = _pending_apply
    if not data:
        return None
    if time.time() - data["at"] > PENDING_APPLY_TTL:
        return None
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else None


@app.get("/api/apply/for-page")
def apply_for_page(url: str = "") -> dict:
    """Fill payload for the form currently open in the browser. Never submits."""
    cfg = _load_cfg()
    from pipeline.fill import fill_payload_for_page

    payload = fill_payload_for_page(
        cfg,
        url,
        pending=_current_pending_payload(),
        public_base=_public_base(),
    )
    if not payload:
        raise HTTPException(
            status_code=404,
            detail="No tailored package matches this form. Open it with Apply on the desk first.",
        )
    return payload


@app.get("/api/apply/profile")
def apply_profile() -> dict:
    """User profile fill fields when no tailored package matches the current page."""
    cfg = _load_cfg()
    from pipeline.fill import fill_fields

    return {
        "package_id": "",
        "company": "",
        "role": "",
        "posting_url": "",
        "apply_url": "",
        "apply_kind": "",
        "fields": fill_fields(cfg),
        "files": {},
        "cached_answers": [],
        "model": str(cfg.get("pipeline.model") or "nvidia/nemotron-3-ultra-550b-a55b").strip(),
        "never_submit": True,
    }


@app.post("/api/apply/answer")
def apply_answer(body: FormAnswerRequest) -> dict:
    """Answer leftover form questions from memory + this role's tailored CV. Never submits."""
    cfg = _load_cfg()
    from pipeline.fill import answer_form_questions, fill_payload_for_page

    package_id = (body.package_id or "").strip()
    if not package_id:
        matched = fill_payload_for_page(
            cfg,
            body.url,
            pending=_current_pending_payload(),
            public_base=_public_base(),
        )
        package_id = str((matched or {}).get("package_id") or "")

    questions = [
        {"key": item.key, "label": item.label, "kind": item.kind, "options": list(item.options or [])}
        for item in body.questions
    ]
    t0 = time.perf_counter()

    if not package_id:
        # Check if requested questions can be answered directly from the profile without a tailored package
        answers, stats = answer_form_questions(
            cfg,
            questions,
            package_id="",
            page_url=body.url,
            feedback=body.feedback,
            with_stats=True,
        )
        if answers and all(not a.get("skip") and a.get("value") for a in answers):
            stats["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            return {"package_id": "", "answers": answers, "stats": stats, "never_submit": True}

        raise HTTPException(
            status_code=404,
            detail="No tailored package matches this form. Open it with Apply on the desk first.",
        )

    answers, stats = answer_form_questions(
        cfg,
        questions,
        package_id=package_id,
        page_url=body.url,
        feedback=body.feedback,
        with_stats=True,
    )
    stats["latency_ms"] = round((time.perf_counter() - t0) * 1000)
    return {"package_id": package_id, "answers": answers, "stats": stats, "never_submit": True}


@app.get("/api/apply/pending")
def apply_pending() -> dict:
    with _apply_lock:
        data = _pending_apply
    if not data:
        return {"payload": None}
    if time.time() - data["at"] > PENDING_APPLY_TTL:
        return {"payload": None}
    return data["payload"]


@app.post("/api/apply/consumed")
def apply_consumed() -> dict:
    global _pending_apply
    with _apply_lock:
        _pending_apply = None
    return {"ok": True}


@app.post("/api/runs")
def start_run(body: RunRequest) -> dict:
    urls = parse_job_urls("\n".join(part for part in (body.urls, body.url) if part))
    jd = (body.jd or "").strip()
    if not urls and not jd:
        raise HTTPException(status_code=400, detail="Paste one or more job URLs or paste a job description.")
    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    thread = threading.Thread(target=_execute_run, args=(run_id, urls, body, sink), daemon=True)
    with _run_lock:
        run = _new_run(run_id, sink, kind="run")
        run["thread"] = thread
        _runs[run_id] = run
    thread.start()
    return {
        "id": run_id,
        "count": len(urls) if urls else 1,
        "company": (body.company or "").strip(),
        "role": (body.role or "").strip(),
        "url": urls[0] if urls else "",
    }


@app.get("/api/runs/active")
def active_run() -> dict:
    _BROWSER_KINDS = {"hunt", "resolve", "unsave"}
    with _run_lock:
        _reclaim_finished_runs_locked()
        all_active = [
            r for r in _runs.values()
            if r.get("status") in {"running", "stopping"}
        ]
        # Prefer the most-recent browser run for the primary run_id the UI tracks
        browser_run = next(
            (r for r in reversed(all_active) if r.get("kind") in _BROWSER_KINDS), None
        )
        tailor_runs = [r for r in all_active if r.get("kind") == "run"]
        run = browser_run or (all_active[-1] if all_active else None)
        if not run:
            return {
                "id": None, "kind": None, "status": None,
                "browser_busy": False, "tailor_count": 0,
            }
        return {
            "id": run["id"],
            "kind": run["kind"],
            "status": run["status"],
            "browser": bool(run.get("browser")),
            "browser_busy": browser_run is not None,
            "tailor_count": len(tailor_runs),
        }


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    with _run_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "id": run["id"],
            "status": run["status"],
            "company": run["company"],
            "role": run["role"],
            "package_id": run["package_id"],
            "error": run["error"],
        }


@app.get("/api/runs/{run_id}/stream")
async def run_stream(run_id: str):
    with _run_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        sink: queue.Queue = run["queue"]

    async def events():
        while True:
            try:
                item = await asyncio.to_thread(sink.get, True, 1.0)
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                continue
            if item is None:
                with _run_lock:
                    snapshot = {
                        "status": _runs[run_id]["status"],
                        "package_id": _runs[run_id]["package_id"],
                        "packages": _runs[run_id].get("packages") or [],
                        "error": _runs[run_id]["error"],
                    }
                yield f"event: done\ndata: {json.dumps(snapshot)}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict:
    with _run_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run["status"] not in {"running", "stopping"}:
            return {"id": run_id, "status": run["status"]}
        event = run.get("stop")
        if event:
            event.set()
        run["status"] = "stopping"
        loop = run.get("loop")
        task = run.get("task")
    if loop is not None and task is not None:
        def cancel_task() -> None:
            if not task.done():
                task.cancel()

        try:
            loop.call_soon_threadsafe(cancel_task)
        except RuntimeError:
            pass
    return {"id": run_id, "status": "stopping"}


@app.post("/api/apply/resolve")
def start_apply_resolve() -> dict:
    """Read Apply links for existing packages. Does not rewrite CVs."""
    if _browser_busy():
        raise HTTPException(status_code=409, detail="Camoufox is already in use. Stop hunt first.")
    cfg = _load_cfg()
    from pipeline.apply_resolve import packages_needing_apply_url

    needed = packages_needing_apply_url(cfg)
    if not needed:
        return {"id": None, "count": 0, "line": "Every tailored posting already has an Apply link."}
    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    thread = threading.Thread(target=_execute_apply_resolve, args=(run_id, sink), daemon=True)
    with _run_lock:
        run = _new_run(run_id, sink, kind="resolve")
        run["thread"] = thread
        _runs[run_id] = run
    thread.start()
    return {"id": run_id, "count": len(needed)}


@app.post("/api/hunt")
def start_hunt(body: HuntRequest) -> dict:
    # Only block on browser-using runs; pure tailor runs (kind="run") can coexist.
    _BROWSER_KINDS = {"hunt", "resolve", "unsave"}
    with _run_lock:
        busy = _active_run_locked()
        # Find a browser-busy run specifically
        browser_busy_run = next(
            (
                r for r in reversed(list(_runs.values()))
                if r.get("status") in {"running", "stopping"}
                and r.get("kind") in _BROWSER_KINDS
            ),
            None,
        )
        thread = (browser_busy_run or {}).get("thread")
        stopping = bool(
            browser_busy_run
            and (
                browser_busy_run.get("status") == "stopping"
                or (browser_busy_run.get("stop") is not None and browser_busy_run["stop"].is_set())
            )
        )
    if browser_busy_run and not stopping:
        raise HTTPException(status_code=409, detail="A hunt or browser task is already running.")
    if browser_busy_run and stopping:
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)
        with _run_lock:
            browser_busy_run = next(
                (
                    r for r in reversed(list(_runs.values()))
                    if r.get("status") in {"running", "stopping"}
                    and r.get("kind") in _BROWSER_KINDS
                ),
                None,
            )
        if browser_busy_run:
            raise HTTPException(
                status_code=409,
                detail="Previous hunt is still closing the browser. Try Hunt again in a few seconds.",
            )
    cfg = _load_cfg()
    cap = hunt_limit(cfg, body.max_jobs)
    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    thread = threading.Thread(target=_execute_hunt, args=(run_id, cap, sink), daemon=True)
    with _run_lock:
        run = _new_run(run_id, sink, kind="hunt")
        run["thread"] = thread
        _runs[run_id] = run
    thread.start()
    return {"id": run_id, "max_jobs": cap}


def _execute_apply_resolve(run_id: str, sink: queue.Queue) -> None:
    import asyncio as aio

    from pipeline.apply_resolve import resolve_stored_apply_urls
    from pipeline.apply_url import is_resolved_apply

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        sink.put(
            {
                "type": "stage",
                "line": "Reading Apply links on already-tailored postings. CVs are not rewritten. Apply is never clicked.",
            }
        )
        cfg = load_config(force=True)

        def on_event(item: dict) -> None:
            _emit_run_event(run_id, sink, item)

        async def _run():
            _bind_run_task(run_id)
            return await resolve_stored_apply_urls(
                cfg,
                on_event=on_event,
                should_stop=lambda: _stop_requested(run_id),
            )

        results = aio.run(_run())
        ready = [
            item.get("package_id")
            for item in results
            if item.get("package_id")
            and is_resolved_apply(item.get("apply_url") or "", item.get("apply_kind") or "")
        ]
        with _run_lock:
            _runs[run_id]["packages"] = [pid for pid in ready if pid]
            _runs[run_id]["package_id"] = ready[0] if ready else None
            stopped = _runs[run_id]["stop"].is_set() or _runs[run_id]["status"] == "stopping"
            if stopped:
                _runs[run_id]["status"] = "stopped"
                _runs[run_id]["error"] = "Stopped"
            else:
                _runs[run_id]["status"] = "done"
        if stopped:
            sink.put({"type": "stage", "line": "Stopped reading Apply links. Links already found are kept."})
        else:
            sink.put(
                {
                    "type": "stage",
                    "line": f"Apply links ready for {len(ready)} posting(s). Easy Apply opens LinkedIn; others open the company form.",
                }
            )
    except aio.CancelledError:
        _mark_stopped(run_id)
        sink.put({"type": "stage", "line": "Stopped reading Apply links. Links already found are kept."})
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)


def _execute_run(run_id: str, urls: list[str], body: RunRequest, sink: queue.Queue) -> None:
    import asyncio as aio

    from pipeline.browser_hunt import hydrate_job_urls
    from pipeline.hunt import JobProgress, _ats_score
    from pipeline.jobs import is_placeholder_company
    from pipeline.search import find_existing_package

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    cfg = load_config(force=True)
    packages: list[str] = []
    board = JobProgress(lambda item: _emit_run_event(run_id, sink, item))

    def stop() -> bool:
        return _stop_requested(run_id)

    async def _hydrate() -> list:
        _bind_run_task(run_id)
        return await hydrate_job_urls(
            cfg,
            urls,
            on_listing=board.found,
            on_stage=board.stage,
            should_stop=stop,
        )

    try:
        extra_jd = (body.jd or "").strip()
        listings: list[dict] = []

        # Emit an immediate placeholder card so the board updates right away,
        # before any URL fetching or JD inference takes place.
        _early_company = (body.company or "").strip()
        _early_role = (body.role or "").strip()
        _early_url = urls[0] if urls else ""
        if _early_company or _early_role or _early_url:
            sink.put({
                "type": "queued",
                "status": "queued",
                "company": _early_company or "Detecting…",
                "role": _early_role or "Detecting…",
                "url": _early_url,
                "line": f"Queued: {_early_company or _early_url or 'JD paste'}",
            })
        elif extra_jd:
            sink.put({
                "type": "queued",
                "status": "queued",
                "company": "Detecting…",
                "role": "Detecting…",
                "url": "",
                "line": "Queued: detecting company and role from description…",
            })

        if extra_jd and urls and len(urls) != 1:
            sink.put({"type": "log", "line": "Pasted description is used with a single URL; reading each posting from the page instead."})
            extra_jd = ""
        if extra_jd:
            from pipeline.jobs import fetch_posting

            sink.put({"type": "stage", "line": "Tailoring from the pasted job description."})
            if urls:
                posting = fetch_posting(urls[0])
                listings = apply_pasted_job_text(
                    [posting] if posting else [],
                    urls,
                    extra_jd,
                    company=body.company,
                    role=body.role,
                    location=body.location,
                )
                if listings:
                    board.found(listings[0])
                if not listings or not listing_has_identity(listings[0]):
                    sink.put({"type": "stage", "line": "Reading the posting for company and role. Camoufox opens for LinkedIn. Apply is never clicked."})
                    browser = aio.run(_hydrate())
                    listings = apply_pasted_job_text(
                        browser or listings,
                        urls,
                        extra_jd,
                        company=body.company,
                        role=body.role,
                        location=body.location,
                    )
            else:
                comp = (body.company or "").strip()
                role = (body.role or "").strip()
                loc = (body.location or "").strip()
                if not comp or not role or not loc:
                    sink.put({"type": "stage", "line": "Detecting company, role, and location from description..."})
                    c_inf, r_inf, l_inf = extract_company_role_from_jd(extra_jd)
                    comp = comp or c_inf
                    role = role or r_inf
                    loc = loc or l_inf
                listing = {
                    "company": comp or "Unknown",
                    "role": role or "Software Engineer",
                    "url": "",
                    "location": loc,
                    "jd": extra_jd,
                    "channel": "desk",
                    "source": "desk:pasted",
                    "apply_url": "",
                    "apply_kind": "",
                }
                listings = [listing]
                board.found(listing)
        else:
            sink.put({"type": "stage", "line": f"Reading {len(urls)} posting(s). Camoufox opens for LinkedIn. Apply is never clicked."})
            listings = aio.run(_hydrate())
        if stop():
            board.stop_unfinished()
            _mark_stopped(run_id, packages)
            sink.put({"type": "stage", "line": "Stopped."})
            return
        if not listings:
            raise RuntimeError("Could not read posting. Please provide a job description or URL.")
        work: list[dict] = []
        for listing in listings:
            company = (listing.get("company") or "").strip()
            if is_placeholder_company(company):
                company = "Unknown"
            role = (listing.get("role") or "Role").strip()
            jd = (listing.get("jd") or "").strip()
            from pipeline.jobs import decorate_listing

            job = decorate_listing(
                {
                    "company": company,
                    "role": role,
                    "url": listing.get("url") or "",
                    "location": (listing.get("location") or body.location or "").strip(),
                    "jd": jd,
                    "channel": "desk",
                    "source": listing.get("source") or "desk",
                    "apply_url": listing.get("apply_url") or "",
                    "apply_kind": listing.get("apply_kind") or "",
                }
            )
            if not jd:
                board.failed(job, f"Skipping {job['url'] or role} — no job description found.")
                continue
            work.append(job)
        board.queue(work)
        from pipeline.llm import worker_count

        workers = worker_count(cfg)

        async def _tailor_work() -> None:
            _bind_run_task(run_id)
            sem = aio.Semaphore(workers)

            async def one(job: dict) -> None:
                async with sem:
                    if stop():
                        board.stopped(job)
                        return
                    company = job["company"]
                    role = job["role"]
                    prior = find_existing_package(cfg, job)
                    if prior:
                        board.ready(job, prior.name, skipped=True, ats_score=_ats_score(prior))
                        packages.append(prior.name)
                        return
                    try:
                        append_job(cfg, job)
                    except Exception as exc:
                        log.warning("Could not append jobs.yaml: %s", exc)
                    board.working(job, "Writing CV")
                    with _run_lock:
                        _runs[run_id]["company"] = company
                        _runs[run_id]["role"] = role
                    try:
                        output_dir = await process_job(
                            job,
                            fill_form=False,
                            on_progress=lambda msg, current=job: board.working(current, msg),
                        )
                    except aio.CancelledError:
                        board.stopped(job)
                        return
                    except Exception as exc:
                        log.exception("Tailor failed for %s — %s", company, role)
                        board.failed(job, str(exc))
                        return
                    if output_dir:
                        packages.append(output_dir.name)
                        board.ready(job, output_dir.name, ats_score=_ats_score(output_dir))
                    else:
                        board.failed(job, f"No package for {company} — {role}")

            if work:
                log.info("Tailoring %s posting(s) with %s worker(s)", len(work), workers)
                await aio.gather(*(one(job) for job in work))

        aio.run(_tailor_work())
        with _run_lock:
            _runs[run_id]["packages"] = packages
            _runs[run_id]["package_id"] = packages[0] if packages else None
            stopped = _runs[run_id]["stop"].is_set() or _runs[run_id]["status"] == "stopping"
            if stopped:
                _runs[run_id]["status"] = "stopped"
                _runs[run_id]["error"] = "Stopped"
            elif packages:
                _runs[run_id]["status"] = "done"
            else:
                _runs[run_id]["status"] = "error"
                _runs[run_id]["error"] = "Pipeline produced no package"
        if stopped:
            board.stop_unfinished()
            sink.put({"type": "stage", "line": "Stopped."})
    except aio.CancelledError:
        board.stop_unfinished()
        _mark_stopped(run_id, packages)
        sink.put({"type": "stage", "line": "Stopped."})
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)


def _execute_hunt(run_id: str, max_jobs: int | None, sink: queue.Queue) -> None:
    import asyncio as aio

    from pipeline.hunt import hunt_and_tailor

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        if max_jobs:
            line = f"Hunting with Camoufox for matching roles (safety cap {max_jobs}). Sign in if a board asks. Apply/Submit is never clicked."
        else:
            line = "Hunting with Camoufox for matching roles. Every posting that passes fit gates is tailored. Sign in if a board asks. Apply/Submit is never clicked."
        sink.put({"type": "stage", "line": line})
        cfg = load_config(force=True)

        def on_event(item: dict) -> None:
            _emit_run_event(run_id, sink, item)

        async def _run():
            _bind_run_task(run_id)
            return await hunt_and_tailor(
                cfg,
                fill_form=False,
                max_jobs=max_jobs,
                on_event=on_event,
                should_stop=lambda: _stop_requested(run_id),
            )

        results = aio.run(_run())
        package_ids = [item["package_id"] for item in results if item.get("package_id")]
        with _run_lock:
            _runs[run_id]["packages"] = package_ids
            _runs[run_id]["package_id"] = package_ids[0] if package_ids else None
            stopped = _runs[run_id]["stop"].is_set() or _runs[run_id]["status"] == "stopping"
            if stopped:
                _runs[run_id]["status"] = "stopped"
                _runs[run_id]["error"] = "Stopped"
            elif package_ids:
                _runs[run_id]["status"] = "done"
            elif results:
                _runs[run_id]["status"] = "error"
                _runs[run_id]["error"] = "Hunt found postings but produced no packages"
            else:
                _runs[run_id]["status"] = "done"
                _runs[run_id]["error"] = "No postings matched the profile after hunt filters"
        if stopped:
            sink.put({"type": "stage", "line": "Hunt stopped. Packages already finished are kept."})
        elif package_ids:
            sink.put({"type": "stage", "line": f"Ready: {len(package_ids)} package(s). You click Submit."})
        else:
            sink.put({"type": "log", "line": "Hunt finished with no new packages."})
    except aio.CancelledError:
        _mark_stopped(run_id)
        sink.put({"type": "stage", "line": "Hunt stopped. Packages already finished are kept."})
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)
