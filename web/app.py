"""Desk UI: submit a job URL, watch the run, read reports."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.config import load_config
from pipeline.jobs import BLOCKED_HOSTS, append_job, fetch_jd, infer_company_role
from pipeline.reports import list_packages, package_detail, package_file
from pipeline.run_pipeline import process_job

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


class RunRequest(BaseModel):
    url: str = ""
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


def _blocked(url: str) -> bool:
    host = url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    return any(host.endswith(b) or host == b for b in BLOCKED_HOSTS)


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
    }


@app.post("/api/inspect")
def inspect(body: InspectRequest) -> dict:
    url = (body.url or "").strip()
    jd = (body.jd or "").strip()
    blocked = bool(url) and _blocked(url)
    fetched = None
    if url and not blocked and not jd:
        fetched = fetch_jd(url)
        if fetched:
            jd = fetched
    company, role = infer_company_role(url, jd)
    return {
        "url": url,
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
    return FileResponse(path, media_type=media, filename=path.name)


@app.post("/api/runs")
def start_run(body: RunRequest) -> dict:
    url = (body.url or "").strip()
    jd = (body.jd or "").strip()
    if not jd:
        raise HTTPException(
            status_code=400,
            detail="Paste the job description. LinkedIn and most ATS pages cannot be scraped.",
        )
    company, role = infer_company_role(url, jd)
    company = (body.company or company or "Unknown").strip()
    role = (body.role or role or "Role").strip()
    job = {
        "company": company,
        "role": role,
        "url": url,
        "location": (body.location or "").strip(),
        "jd": jd,
    }
    cfg = load_config(force=True)
    try:
        append_job(cfg, job)
    except Exception as exc:
        log.warning("Could not append jobs.yaml: %s", exc)

    run_id = uuid.uuid4().hex[:10]
    sink: queue.Queue = queue.Queue()
    with _run_lock:
        _runs[run_id] = {
            "id": run_id,
            "status": "running",
            "company": company,
            "role": role,
            "queue": sink,
            "package_id": None,
            "error": None,
        }

    thread = threading.Thread(target=_execute_run, args=(run_id, job, sink), daemon=True)
    thread.start()
    return {"id": run_id, "company": company, "role": role}


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
                        "error": _runs[run_id]["error"],
                    }
                yield f"event: done\ndata: {json.dumps(snapshot)}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _execute_run(run_id: str, job: dict, sink: queue.Queue) -> None:
    import asyncio as aio

    handler = QueueLogHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        sink.put({"type": "stage", "line": f"Starting tailor for {job['company']} — {job['role']}"})
        output_dir = aio.run(process_job(job, fill_form=False))
        with _run_lock:
            _runs[run_id]["status"] = "done" if output_dir else "error"
            _runs[run_id]["package_id"] = output_dir.name if output_dir else None
            if output_dir is None:
                _runs[run_id]["error"] = "Pipeline produced no package"
    except Exception as exc:
        with _run_lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        sink.put({"type": "log", "line": f"ERROR: {exc}"})
    finally:
        root.removeHandler(handler)
        sink.put(None)
