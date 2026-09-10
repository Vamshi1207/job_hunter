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
    fabrication_freedom_max,
    honesty_gate,
    level_label,
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


async def process_job(
    job: dict,
    fill_form: bool = False,
    on_progress=None,
    cfg=None,
) -> Path | None:
    cfg = cfg or load_config()

    from pipeline.search import find_existing_package

    def note(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception as exc:
                log.debug("on_progress callback exception: %s", exc)

    note("Checking existing")
    existing = find_existing_package(cfg, job)
    if existing is not None:
        log.info(
            "Found existing package for %s — skipping tailor. Directory: %s",
            job.get("company"),
            existing.name,
        )
        return existing

    company, role, jd_text = job["company"], job["role"], job["jd"]
    max_attempts = int(cfg.get("pipeline.max_attempts", 3))
    threshold = int(cfg.get("pipeline.ats_threshold", 80))
    choice = choose_fabrication_freedom(cfg, jd_text, job)
    freedom = int(choice["level"])
    max_freedom = fabrication_freedom_max(cfg)
    min_honesty = honesty_gate(freedom, threshold)
    job_cfg = config_with_freedom(cfg, freedom)
    job["fabrication_freedom"] = freedom
    job["fabrication_freedom_mode"] = choice.get("mode") or "auto"
    job["fabrication_freedom_reason"] = choice.get("reason") or ""
    source = source_of_truth_text(job_cfg)

    log.info(
        "Processing %s — %s (freedom=%s %s, max=%s, ats>=%s, honesty>=%s) — %s",
        company,
        role,
        freedom,
        choice.get("mode"),
        max_freedom,
        threshold,
        min_honesty,
        choice.get("reason"),
    )
    note(f"Freedom {freedom} ({choice.get('mode') or 'auto'})")

    feedback_history = ""
    winning_output = ""
    winning_eval: dict = {}
    winning_cfg = job_cfg
    winning_freedom = freedom
    has_passed = False
    attempts_history: list[dict] = []

    from unittest.mock import patch

    for attempt in range(1, max_attempts + 1):
        log.info("Tailor attempt %s/%s (freedom=%s)", attempt, max_attempts, freedom)
        writing = "Writing CV" if attempt == 1 else "Rewriting CV"
        if max_attempts > 1:
            writing = f"{writing} ({attempt}/{max_attempts}, L{freedom})"
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
                "and complete LinkedIn DM and Why I Fit sections without truncation.\n"
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

        passed = bool(score >= threshold and honesty >= min_honesty)
        attempt_record = {
            "attempt": attempt,
            "freedom": freedom,
            "score": score,
            "honesty": honesty,
            "min_honesty": min_honesty,
            "threshold": threshold,
            "keyword_coverage": int(eval_result.get("keyword_coverage") or score),
            "critique": critique,
            "gaps": list(eval_result.get("gaps") or []),
            "passed": passed,
            "action": "accepted" if passed else "",
        }
        attempts_history.append(attempt_record)

        log.info("Score %s/100 (honesty %s/100, freedom %s, passed=%s)", score, honesty, freedom, passed)
        log.info("Critique: %s", critique)

        # Winning attempt selection:
        # A passed attempt ALWAYS takes precedence over failing attempts.
        # If no attempt passes, retain the best composite quality attempt (min of score and honesty).
        if passed:
            if not has_passed or score > winning_eval.get("score", -1):
                has_passed = True
                winning_output = llm_output
                winning_eval = dict(eval_result)
                winning_cfg = job_cfg
                winning_freedom = freedom
            log.info(
                "Threshold reached: score %s>=%s, honesty %s>=%s (freedom %s).",
                score,
                threshold,
                honesty,
                min_honesty,
                freedom,
            )
            break
        else:
            if not has_passed:
                current_quality = min(score, honesty)
                best_quality = min(winning_eval.get("score", -1), winning_eval.get("honesty", -1))
                if not winning_output or current_quality > best_quality or (current_quality == best_quality and score > winning_eval.get("score", -1)):
                    winning_output = llm_output
                    winning_eval = dict(eval_result)
                    winning_cfg = job_cfg
                    winning_freedom = freedom

        # Dynamic bidirectional freedom adaptation for next attempt
        adaptation_reason = ""
        if attempt < max_attempts:
            old_freedom = freedom
            if honesty < min_honesty:
                # Model over-hallucinated or exceeded allowed boundaries -> decrement
                freedom = max(0, freedom - 1)
                adaptation_reason = f"Honesty low ({honesty} < {min_honesty}) → L{freedom}"
                attempt_record["action"] = f"deescalate_honesty (L{old_freedom} -> L{freedom})"
                note(f"Honesty low → L{freedom}")
                log.warning(
                    "Attempt %s failed honesty gate (%s < %s). Lowering freedom: %s",
                    attempt,
                    honesty,
                    min_honesty,
                    adaptation_reason,
                )
            elif score < threshold:
                # Model was truthful but missed keyword requirements -> increment
                if freedom < max_freedom:
                    freedom = min(max_freedom, freedom + 1)
                    adaptation_reason = f"ATS low ({score} < {threshold}) → L{freedom}"
                    attempt_record["action"] = f"escalate_keyword_gap (L{old_freedom} -> L{freedom})"
                    note(f"ATS low → L{freedom}")
                    log.info(
                        "Attempt %s honest (%s >= %s) but ATS below threshold (%s < %s). Raising freedom: %s",
                        attempt,
                        honesty,
                        min_honesty,
                        score,
                        threshold,
                        adaptation_reason,
                    )
                else:
                    adaptation_reason = f"ATS low ({score} < {threshold}) but already at cap L{max_freedom} → maintain L{freedom}"
                    attempt_record["action"] = f"maintain_cap (L{freedom})"
                    note(f"At cap L{freedom} · refining")
                    log.info(
                        "Attempt %s honest (%s >= %s) but ATS below threshold (%s < %s), already at cap L%s.",
                        attempt,
                        honesty,
                        min_honesty,
                        score,
                        threshold,
                        max_freedom,
                    )
            else:
                adaptation_reason = f"Gates met but not passed → maintain L{freedom}"
                attempt_record["action"] = f"maintain (L{freedom})"

            min_honesty = honesty_gate(freedom, threshold)
            job_cfg = config_with_freedom(cfg, freedom)
            source = source_of_truth_text(job_cfg)

        log.warning(
            "Below gates (need score>=%s honesty>=%s at freedom %s). Refining.",
            threshold,
            min_honesty,
            freedom,
        )
        feedback_history += (
            f"\nAttempt {attempt} score={score} honesty={honesty} freedom={attempt_record['freedom']} "
            f"(need score>={threshold}, honesty>={attempt_record['min_honesty']})\n"
            f"Adaptation: {adaptation_reason or 'Refining attempt'}.\n"
            f"Freedom for next attempt: L{freedom} ({level_label(freedom)}).\n"
            f"Critique: {critique}\n"
            f"Gaps: {eval_result.get('gaps')}\n"
            f"Also protect the {job_cfg.cv_pages}-page budget — drop lower-value bullets if needed.\n"
        )

    if not winning_output:
        log.error("No usable output for %s — skipping save.", company)
        return None

    winning_eval["attempts"] = attempts_history
    winning_eval["fabrication_freedom"] = winning_freedom
    winning_eval["passed"] = has_passed
    job["fabrication_freedom"] = winning_freedom
    job["attempts"] = attempts_history
    job["passed"] = has_passed

    log.info(
        "Saved package for %s at winning freedom L%s (passed=%s, %s attempt(s))",
        company,
        winning_freedom,
        has_passed,
        len(attempts_history),
    )

    note("Saving package")
    with patch("pipeline.tailor.load_config", return_value=winning_cfg):
        output_dir = await save_materials(
            company,
            role,
            winning_output,
            eval_result=winning_eval,
            feedback_history=feedback_history,
            job=job,
            on_progress=on_progress,
        )

    pdf_path = output_dir / f"{winning_cfg.cv_stem}.pdf"
    cl_path = output_dir / "cover_letter.md"
    why_path = output_dir / "why_i_fit.txt"
    playbook = render_playbook(winning_cfg, job, output_dir, pdf_path, cl_path, why_path)
    (output_dir / "playbook.md").write_text(playbook)

    channel = job.get("channel") or "jobs.yaml"
    status_label = "✏️ draft" if has_passed else "⚠️ failed gates"
    append_tracker(
        cfg.tracker_path,
        f"| {date.today().isoformat()} | {company} | {role} | {channel} | {status_label} | {output_dir} | |",
    )

    log.info("[REVIEW] Materials ready for %s", company)
    log.info("  PDF: %s", pdf_path)
    log.info("  Cover letter: %s", cl_path)
    log.info("  Playbook: %s", output_dir / "playbook.md")
    log.info("  Open the PDF, edit if needed, then paste from playbook.md. You click Submit.")

    if fill_form and has_passed:
        from pipeline.apply_bot import apply_to_job

        await apply_to_job(job.get("url") or "", str(pdf_path), str(cl_path))
    elif fill_form and not has_passed:
        log.warning(
            "Skipping form auto-fill for %s: gates not passed (score %s, honesty %s).",
            company,
            winning_eval.get("score"),
            winning_eval.get("honesty"),
        )
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
    parser.add_argument(
        "--cover-letter",
        dest="cover_letter",
        action="store_true",
        default=None,
        help="Generate cover letters for tailored packages.",
    )
    parser.add_argument(
        "--no-cover-letter",
        dest="cover_letter",
        action="store_false",
        help="Skip cover letter generation to save tokens and speed up runs.",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.cover_letter is not None:
        cfg.data.setdefault("pipeline", {})["generate_cover_letter"] = args.cover_letter
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
