"""Search public boards for roles that fit the profile, then tailor each one."""

from __future__ import annotations

import logging

from pipeline.config import Config
from pipeline.jobs import append_job
from pipeline.search import hunt_limit, search_jobs_async

log = logging.getLogger(__name__)


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

    listings = await search_jobs_async(cfg, limit=max_jobs)
    cap = hunt_limit(cfg, max_jobs)
    if not listings:
        log.warning(
            "Hunt found no matching postings (cap %s). Check hunt.sources, Camoufox login, and fit filters.",
            cap,
        )
        return []

    log.info("Hunt selected %s posting(s) to tailor (cap %s).", len(listings), cap)
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
            if on_event:
                on_event(
                    {
                        "type": "package",
                        "package_id": prior.name,
                        "company": job["company"],
                        "role": job["role"],
                        "line": f"Already processed {job['company']} — {job['role']}. Skipping.",
                    }
                )
            results.append(
                {
                    "company": job["company"],
                    "role": job["role"],
                    "url": job["url"],
                    "package_id": prior.name,
                }
            )
            continue
        if on_event:
            on_event(
                {
                    "type": "processing",
                    "company": job["company"],
                    "role": job["role"],
                    "line": f"Starting tailor for {job['company']} — {job['role']}",
                }
            )
        output_dir = await process_job(job, fill_form=fill_form)
        if output_dir:
            try:
                append_job(cfg, job)
            except Exception as exc:
                log.warning("Could not append jobs.yaml: %s", exc)
            if on_event:
                on_event(
                    {
                        "type": "package",
                        "package_id": output_dir.name,
                        "company": job["company"],
                        "role": job["role"],
                        "line": f"Ready: {job['company']} — {job['role']}",
                    }
                )
        results.append(
            {
                "company": job["company"],
                "role": job["role"],
                "url": job["url"],
                "package_id": output_dir.name if output_dir else None,
            }
        )
    return results
