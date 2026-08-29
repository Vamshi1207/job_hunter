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
from pipeline.jobs import load_jobs
from pipeline.playbook import render_playbook
from pipeline.tailor import (
    evaluate_ats_score,
    generate_tailored_materials,
    parse_tagged_output,
    resume_plain_text,
    save_materials,
    source_of_truth_text,
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


async def process_job(job: dict, fill_form: bool) -> Path | None:
    cfg = load_config()
    company, role, jd_text = job["company"], job["role"], job["jd"]
    max_attempts = int(cfg.get("pipeline.max_attempts", 3))
    threshold = int(cfg.get("pipeline.ats_threshold", 80))
    source = source_of_truth_text(cfg)

    log.info("Processing %s — %s", company, role)

    feedback_history = ""
    best_output = ""
    best_score = -1
    best_eval: dict = {}

    for attempt in range(1, max_attempts + 1):
        log.info("Tailor attempt %s/%s", attempt, max_attempts)
        llm_output = generate_tailored_materials(company, role, jd_text, feedback_history)
        if not llm_output.strip():
            log.error("Empty LLM output on attempt %s", attempt)
            continue

        parsed = parse_tagged_output(llm_output)
        if not parsed.get("TITLE") and not parsed.get("SUMMARY"):
            log.error("Could not parse tagged resume output on attempt %s", attempt)
            continue
        plain = resume_plain_text(parsed)
        eval_result = evaluate_ats_score(jd_text, plain, source)
        score = int(eval_result.get("score") or 0)
        honesty = int(eval_result.get("honesty") or 0)
        critique = eval_result.get("critique") or "No critique provided."

        log.info("Score %s/100 (honesty %s/100)", score, honesty)
        log.info("Critique: %s", critique)

        if score > best_score:
            best_score = score
            best_output = llm_output
            best_eval = eval_result

        if score >= threshold and honesty >= threshold:
            log.info("Threshold %s reached with honest materials.", threshold)
            break

        log.warning("Below threshold or honesty gate. Refining without inventing.")
        feedback_history += (
            f"\nAttempt {attempt} score={score} honesty={honesty}\n"
            f"Critique: {critique}\n"
            f"Gaps: {eval_result.get('gaps')}\n"
        )

    if not best_output:
        log.error("No usable output for %s — skipping save.", company)
        return None

    output_dir = await save_materials(
        company,
        role,
        best_output,
        eval_result=best_eval,
        feedback_history=feedback_history,
    )

    pdf_path = output_dir / f"{cfg.cv_stem}.pdf"
    cl_path = output_dir / "cover_letter.md"
    why_path = output_dir / "why_i_fit.txt"
    playbook = render_playbook(cfg, job, output_dir, pdf_path, cl_path, why_path)
    (output_dir / "playbook.md").write_text(playbook)

    append_tracker(
        cfg.tracker_path,
        f"| {date.today().isoformat()} | {company} | {role} | jobs.yaml | ✏️ draft | {output_dir} | |",
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
        description="Tailor CV + cover letter for jobs listed in jobs.yaml. Never submits."
    )
    parser.add_argument("--job", help="Only process jobs whose company contains this string")
    parser.add_argument(
        "--fill-form",
        action="store_true",
        help="Best-effort Greenhouse/Lever fill + screenshot. Still does not click Submit.",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    try:
        jobs = load_jobs(cfg, company_filter=args.job)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if not jobs:
        log.error("No jobs to process. Add entries under `jobs:` in %s", cfg.jobs_path)
        return 1

    log.info("Loaded %s job(s) from %s", len(jobs), cfg.jobs_path)
    for job in jobs:
        await process_job(job, fill_form=args.fill_form)
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
