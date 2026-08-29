"""Desk UI: submit a job URL, watch the run, read reports."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.config import load_config
from pipeline.jobs import append_job, apply_pasted_job_text, infer_company_role, listing_has_identity, parse_job_urls
from pipeline.reports import list_packages, package_detail, package_file
from pipeline.run_pipeline import process_job
from pipeline.search import hunt_limit, target_markets, target_roles

WEB_ROOT = Path(__file__).resolve().parent
STATIC = WEB_ROOT / "static"

app = FastAPI(title="Job desk")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

log = logging.getLogger("jobdesk")

_runs: dict[str, dict] = {}
_run_lock = threading.Lock()


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
    cfg = load_config(force=True)
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
        "hunt": {
            "max_jobs": hunt_limit(cfg),
            "roles": target_roles(cfg),
            "markets": target_markets(cfg),
            "years_experience": cfg.get("hunt.years_experience", cfg.get("career.years_experience")),
            "years_buffer": cfg.get("hunt.years_buffer", 2),
            "exclude_levels": cfg.get("hunt.exclude_levels") or [],
            "reject_skills": cfg.get("hunt.reject_skills") or [],
        },
    }


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
    cfg = load_config(force=True)
    return {"packages": list_packages(cfg)}


@app.get("/api/packages/{package_id}")
def package(package_id: str) -> dict:
    cfg = load_config(force=True)
    detail = package_detail(cfg, package_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return detail


@app.get("/api/packages/{package_id}/file/{filename}")
def package_download(package_id: str, filename: str):
    cfg = load_config(force=True)
    path = package_file(cfg, package_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    media = "application/pdf" if path.suffix == ".pdf" else "text/plain; charset=utf-8"
    if path.suffix.lower() == ".html":
        media = "text/html; charset=utf-8"
    return FileResponse(path, media_type=media, content_disposition_type="inline")


@app.post("/api/runs")
def start_run(body: RunRequest) -> dict:
    urls = parse_job_urls("\n".join(part for part in (body.urls, body.url) if part))
    if not urls:
        raise HTTPException(status_code=400, detail="Paste one or more job URLs, one per line.")
    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    with _run_lock:
        _runs[run_id] = {
            "id": run_id,
            "status": "running",
            "company": "",
            "role": "",
            "queue": sink,
            "package_id": None,
            "packages": [],
            "error": None,
        }

    thread = threading.Thread(target=_execute_run, args=(run_id, urls, body, sink), daemon=True)
    thread.start()
    return {"id": run_id, "count": len(urls)}


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


@app.post("/api/hunt")
def start_hunt(body: HuntRequest) -> dict:
    with _run_lock:
        busy = any(run.get("kind") == "hunt" and run.get("status") == "running" for run in _runs.values())
    if busy:
        raise HTTPException(status_code=409, detail="A hunt is already running.")
    cfg = load_config(force=True)
    cap = hunt_limit(cfg, body.max_jobs)
    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    with _run_lock:
        _runs[run_id] = {
            "id": run_id,
            "kind": "hunt",
            "status": "running",
            "company": "",
            "role": "",
            "queue": sink,
            "package_id": None,
            "packages": [],
            "error": None,
        }
    thread = threading.Thread(target=_execute_hunt, args=(run_id, cap, sink), daemon=True)
    thread.start()
    return {"id": run_id, "max_jobs": cap}


def _execute_run(run_id: str, urls: list[str], body: RunRequest, sink: queue.Queue) -> None:
    import asyncio as aio

    from pipeline.browser_hunt import hydrate_job_urls
    from pipeline.jobs import is_placeholder_company
    from pipeline.search import find_existing_package

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    cfg = load_config(force=True)
    packages: list[str] = []
    try:
        extra_jd = (body.jd or "").strip()
        listings: list[dict] = []
        if extra_jd and len(urls) != 1:
            sink.put({"type": "log", "line": "Pasted description is used with a single URL; reading each posting from the page instead."})
            extra_jd = ""
        if extra_jd:
            from pipeline.jobs import fetch_posting

            sink.put({"type": "stage", "line": "Tailoring from the pasted job description."})
            posting = fetch_posting(urls[0])
            listings = apply_pasted_job_text(
                [posting] if posting else [],
                urls,
                extra_jd,
                company=body.company,
                role=body.role,
                location=body.location,
            )
            if not listing_has_identity(listings[0]):
                sink.put({"type": "stage", "line": "Reading the posting for company and role. Camoufox opens for LinkedIn. Apply is never clicked."})
                browser = aio.run(hydrate_job_urls(cfg, urls))
                listings = apply_pasted_job_text(
                    browser or listings,
                    urls,
                    extra_jd,
                    company=body.company,
                    role=body.role,
                    location=body.location,
                )
        else:
            sink.put({"type": "stage", "line": f"Reading {len(urls)} posting(s). Camoufox opens for LinkedIn. Apply is never clicked."})
            listings = aio.run(hydrate_job_urls(cfg, urls))
        if not listings:
            raise RuntimeError("Could not read those URLs. Sign in to LinkedIn in the Camoufox window if asked, then run again.")
        for listing in listings:
            company = (listing.get("company") or "").strip()
            if is_placeholder_company(company):
                company = "Unknown"
            role = (listing.get("role") or "Role").strip()
            jd = (listing.get("jd") or "").strip()
            if not jd:
                sink.put({"type": "log", "line": f"Skipping {listing.get('url')} — no job description found."})
                continue
            job = {
                "company": company,
                "role": role,
                "url": listing.get("url") or "",
                "location": (listing.get("location") or body.location or "").strip(),
                "jd": jd,
                "channel": "desk",
                "source": listing.get("source") or "desk",
            }
            prior = find_existing_package(cfg, job)
            if prior:
                sink.put(
                    {
                        "type": "package",
                        "package_id": prior.name,
                        "company": company,
                        "role": role,
                        "line": f"Already processed {company} — {role}. Skipping.",
                    }
                )
                packages.append(prior.name)
                continue
            try:
                append_job(cfg, job)
            except Exception as exc:
                log.warning("Could not append jobs.yaml: %s", exc)
            sink.put({"type": "processing", "company": company, "role": role, "line": f"Starting tailor for {company} — {role}"})
            with _run_lock:
                _runs[run_id]["company"] = company
                _runs[run_id]["role"] = role
            output_dir = aio.run(process_job(job, fill_form=False))
            if output_dir:
                packages.append(output_dir.name)
                sink.put(
                    {
                        "type": "package",
                        "package_id": output_dir.name,
                        "company": company,
                        "role": role,
                        "line": f"Ready: {company} — {role}",
                    }
                )
        with _run_lock:
            _runs[run_id]["packages"] = packages
            _runs[run_id]["package_id"] = packages[0] if packages else None
            if packages:
                _runs[run_id]["status"] = "done"
            else:
                _runs[run_id]["status"] = "error"
                _runs[run_id]["error"] = "Pipeline produced no package"
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)


def _execute_hunt(run_id: str, max_jobs: int, sink: queue.Queue) -> None:
    import asyncio as aio

    from pipeline.hunt import hunt_and_tailor

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        sink.put({"type": "stage", "line": f"Hunting with Camoufox for up to {max_jobs} matching role(s). Sign in if a board asks. Apply/Submit is never clicked."})
        cfg = load_config(force=True)

        def on_event(item: dict) -> None:
            sink.put(item)

        results = aio.run(hunt_and_tailor(cfg, fill_form=False, max_jobs=max_jobs, on_event=on_event))
        package_ids = [item["package_id"] for item in results if item.get("package_id")]
        with _run_lock:
            _runs[run_id]["packages"] = package_ids
            _runs[run_id]["package_id"] = package_ids[0] if package_ids else None
            if package_ids:
                _runs[run_id]["status"] = "done"
            elif results:
                _runs[run_id]["status"] = "error"
                _runs[run_id]["error"] = "Hunt found postings but produced no packages"
            else:
                _runs[run_id]["status"] = "done"
                _runs[run_id]["error"] = "No postings matched the profile after hunt filters"
        if package_ids:
            sink.put({"type": "stage", "line": f"Ready: {len(package_ids)} package(s). You click Submit."})
        else:
            sink.put({"type": "log", "line": "Hunt finished with no new packages."})
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)
