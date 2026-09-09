"""Generate tailored application packages from jobs.yaml. Never clicks Submit."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.fabrication import (
    choose_fabrication_freedom,
    config_with_freedom,
    honesty_gate,
)
from pipeline.jobs import load_jobs
from pipeline.playbook import render_playbook
from pipeline.tailor import (
    evaluate_ats_score,
    generate_tailored_materials,
    parse_tagged_output,
    resume_plain_text,
    save_materials,
    source_of_truth_text,
    validate_tailored_output,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

TRACKER_HEADER = (
    "| Date | Company | Role | Channel | Status | Folder | Follow-up |\n"
    "|---|---|---|---|---|---|---|\n"
)


def append_tracker(tracker_path: Path, row: str) -> None:
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    existing = tracker_path.read_text() if tracker_path.exists() else ""
    if not existing.startswith("| Date |"):
        tracker_path.write_text(TRACKER_HEADER)
    with tracker_path.open("a") as handle:
        handle.write(row + "\n")


async def process_job(job: dict, fill_form: bool, on_progress=None) -> Path | None:
    cfg = load_config()
    from pipeline.search import find_existing_package

    def note(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    existing = find_existing_package(cfg, job)
    if existing is not None:
        log.info(
            "Already processed %s — %s (%s). Skipping.",
            job.get("company"),
            job.get("role"),
            existing.name,
        )
        return existing

    company, role, jd_text = job["company"], job["role"], job["jd"]
    max_attempts = int(cfg.get("pipeline.max_attempts", 3))
    threshold = int(cfg.get("pipeline.ats_threshold", 80))
    choice = choose_fabrication_freedom(cfg, jd_text, job)
    freedom = int(choice["level"])
    min_honesty = honesty_gate(freedom, threshold)
    job_cfg = config_with_freedom(cfg, freedom)
    job["fabrication_freedom"] = freedom
    job["fabrication_freedom_mode"] = choice.get("mode") or "auto"
    job["fabrication_freedom_reason"] = choice.get("reason") or ""
    source = source_of_truth_text(job_cfg)

    log.info(
        "Processing %s — %s (freedom=%s %s, ats>=%s, honesty>=%s) — %s",
        company,
        role,
        freedom,
        choice.get("mode"),
        threshold,
        min_honesty,
        choice.get("reason"),
    )
    note(f"Freedom {freedom} ({choice.get('mode') or 'auto'})")

    feedback_history = ""
    best_output = ""
    best_score = -1
    best_eval: dict = {}

    from unittest.mock import patch

    for attempt in range(1, max_attempts + 1):
        log.info("Tailor attempt %s/%s", attempt, max_attempts)
        writing = "Writing CV" if attempt == 1 else "Rewriting CV"
        if max_attempts > 1:
            writing = f"{writing} ({attempt}/{max_attempts})"
        note(writing)
        with patch("pipeline.tailor.load_config", return_value=job_cfg):
            llm_output = await asyncio.to_thread(
                generate_tailored_materials, company, role, jd_text, feedback_history
            )
        if not llm_output.strip():
            log.error("Empty LLM output on attempt %s", attempt)
            continue

        parsed = parse_tagged_output(llm_output, job_cfg)
        is_valid, validation_errors = validate_tailored_output(parsed, job_cfg)
        if not is_valid:
            log.error(
                "Tailor attempt %s produced incomplete output: %s",
                attempt,
                "; ".join(validation_errors),
            )
            feedback_history += (
                f"\nAttempt {attempt} REJECTED - INCOMPLETE MATERIALS:\n"
                f"Issues: {'; '.join(validation_errors)}\n"
                "You MUST output all employer titles, at least 2 bullets per employer, all Key Skills categories, "
                "and complete Cover Letter, LinkedIn DM, and Why I Fit sections without truncation.\n"
            )
            continue
        scoring = "Scoring ATS"
        if max_attempts > 1:
            scoring = f"{scoring} ({attempt}/{max_attempts})"
        note(scoring)
        plain = resume_plain_text(parsed, job_cfg)
        with patch("pipeline.tailor.load_config", return_value=job_cfg):
            eval_result = await asyncio.to_thread(evaluate_ats_score, jd_text, plain, source)
        score = int(eval_result.get("score") or 0)
        honesty = int(eval_result.get("honesty") or 0)
        critique = eval_result.get("critique") or "No critique provided."
        eval_result["fabrication_freedom"] = freedom
        eval_result["fabrication_freedom_mode"] = choice.get("mode")
        eval_result["fabrication_freedom_reason"] = choice.get("reason")

        log.info("Score %s/100 (honesty %s/100, freedom %s)", score, honesty, freedom)
        log.info("Critique: %s", critique)

        if score > best_score:
            best_score = score
            best_output = llm_output
            best_eval = eval_result

        if score >= threshold and honesty >= min_honesty:
            log.info(
                "Threshold reached: score %s>=%s, honesty %s>=%s (freedom %s).",
                score,
                threshold,
                honesty,
                min_honesty,
                freedom,
            )
            break

        log.warning(
            "Below gates (need score>=%s honesty>=%s at freedom %s). Refining.",
            threshold,
            min_honesty,
            freedom,
        )
        feedback_history += (
            f"\nAttempt {attempt} score={score} honesty={honesty} freedom={freedom} "
            f"(need score>={threshold}, honesty>={min_honesty})\n"
            f"Critique: {critique}\n"
            f"Gaps: {eval_result.get('gaps')}\n"
            f"Also protect the {job_cfg.cv_pages}-page budget — drop lower-value bullets if needed.\n"
        )

    if not best_output:
        log.error("No usable output for %s — skipping save.", company)
        return None

    note("Saving package")
    with patch("pipeline.tailor.load_config", return_value=job_cfg):
        output_dir = await save_materials(
            company,
            role,
            best_output,
            eval_result=best_eval,
            feedback_history=feedback_history,
            job=job,
            on_progress=on_progress,
        )

    pdf_path = output_dir / f"{cfg.cv_stem}.pdf"
    cl_path = output_dir / "cover_letter.md"
    why_path = output_dir / "why_i_fit.txt"
    playbook = render_playbook(cfg, job, output_dir, pdf_path, cl_path, why_path)
    (output_dir / "playbook.md").write_text(playbook)

    channel = job.get("channel") or "jobs.yaml"
    append_tracker(
        cfg.tracker_path,
        f"| {date.today().isoformat()} | {company} | {role} | {channel} | ✏️ draft | {output_dir} | |",
    )

    log.info("[REVIEW] Materials ready for %s", company)
    log.info("  PDF: %s", pdf_path)
    log.info("  Cover letter: %s", cl_path)
    log.info("  Playbook: %s", output_dir / "playbook.md")
    log.info("  Open the PDF, edit if needed, then paste from playbook.md. You click Submit.")

    if fill_form:
        from pipeline.apply_bot import apply_to_job

        await apply_to_job(job.get("url") or "", str(pdf_path), str(cl_path))
    return output_dir


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Search public boards and/or tailor CV + cover letter. Never submits."
    )
    parser.add_argument("--job", help="Only process jobs whose company contains this string")
    parser.add_argument(
        "--hunt",
        action="store_true",
        help="Search public boards for roles that match config.yaml, then tailor each one.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Safety ceiling for --hunt (default hunt.max_jobs; 0 = every match).",
    )
    parser.add_argument(
        "--fill-form",
        action="store_true",
        help="Best-effort Greenhouse/Lever fill + screenshot. Still does not click Submit.",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.hunt:
        from pipeline.hunt import hunt_and_tailor

        results = await hunt_and_tailor(cfg, fill_form=args.fill_form, max_jobs=args.max_jobs)
        return 0 if results else 1

    try:
        jobs = load_jobs(cfg, company_filter=args.job)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if not jobs:
        log.error("No jobs to process. Add entries under `jobs:` in %s, or pass --hunt.", cfg.jobs_path)
        return 1

    log.info("Loaded %s job(s) from %s", len(jobs), cfg.jobs_path)
    from pipeline.llm import worker_count

    workers = worker_count(cfg)
    log.info("Tailoring with %s worker(s)", workers)
    sem = asyncio.Semaphore(workers)

    async def _one(job: dict) -> None:
        async with sem:
            await process_job(job, fill_form=args.fill_form)

    await asyncio.gather(*(_one(job) for job in jobs))
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
