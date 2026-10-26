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
    evaluate_voice,
    generate_jd_brief,
    generate_tailored_materials,
    jd_brief_enabled,
    job_blocks,
    normalize_parsed,
    parse_tagged_output,
    resume_plain_text,
    revise_for_voice,
    save_materials,
    source_of_truth_text,
    validate_tailored_output,
    voice_gate_enabled,
    voice_passes,
    voice_rewrite_enabled,
    voice_threshold,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

TRACKER_HEADER = (
    "| Date | Company | Role | Channel | Status | Folder | Follow-up |\n"
    "|---|---|---|---|---|---|---|\n"
)


def draft_quality(eval_result: dict, voice_gate: bool = True) -> float:
    """Rank failing drafts. Voice and honesty matter as much as a 1-point ATS gap."""
    score = int(eval_result.get("score") or 0)
    honesty = int(eval_result.get("honesty") or 0)
    if not voice_gate:
        return float(min(score, honesty))
    voice_score = int(eval_result.get("voice_score") or 0)
    return 0.3 * score + 0.3 * honesty + 0.4 * voice_score


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
    force: bool = False,
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
    if not force:
        existing = find_existing_package(cfg, job)
        if existing is not None:
            log.info(
                "Found existing package for %s — skipping tailor. Directory: %s",
                job.get("company"),
                existing.name,
            )
            return existing

    company, role, jd_text = job["company"], job["role"], job["jd"]
    try:
        liveness_on = bool(cfg.get("pipeline.liveness_check", True))
    except Exception:
        liveness_on = True
    if liveness_on:
        from pipeline.liveness import is_expired

        if is_expired(jd_text or "", status_code=None, has_apply=None):
            log.warning(
                "Skipping %s — %s: posting reads closed (liveness gate). No LLM spent.",
                company,
                role,
            )
            note("Posting closed — skipped")
            return None
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
    voice_gate = voice_gate_enabled(job_cfg)
    voice_floor = voice_threshold(job_cfg)
    try:
        verify_mode = str(cfg.get("verification.mode", "enforce") or "enforce").strip().lower()
    except Exception:
        verify_mode = "enforce"
    if verify_mode not in ("enforce", "advisory"):
        verify_mode = "enforce"
    _verify_titles_base: list[dict] = []
    _verify_years: float | None = None
    if verify_mode == "enforce":
        try:
            _verify_titles_base = [
                {
                    "prefix": _job.get("prefix"),
                    "employer": _job.get("employer"),
                    "canonical": _job.get("default_title"),
                }
                for _job in job_blocks(cfg)
            ]
            _verify_years = float(cfg.get("career.years_experience") or 0) or None
        except (TypeError, ValueError):
            _verify_years = None

    from unittest.mock import patch

    jd_brief: dict = {}
    if jd_brief_enabled(job_cfg):
        note("Reading JD")
        with patch("pipeline.tailor.load_config", return_value=job_cfg):
            jd_brief = await asyncio.to_thread(
                generate_jd_brief, company, role, jd_text
            )
        if jd_brief:
            log.info("JD fit score %s/100", jd_brief.get("match_score"))
            note(f"Fit {jd_brief.get('match_score')}/100")
        try:
            from pipeline.verify import check_skill_gaps

            jd_brief["skill_gaps"] = check_skill_gaps(jd_text, source)
        except Exception as exc:
            log.debug("Skill-gap check failed for %s: %s", company, exc)

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
    voice_revision_used = False
    blocked_history: list[dict] = []

    async def evaluate_draft(parsed: dict):
        plain = resume_plain_text(parsed, job_cfg)
        with patch("pipeline.tailor.load_config", return_value=job_cfg):
            ats_call = asyncio.to_thread(evaluate_ats_score, jd_text, plain, source)
            if not voice_gate:
                ats_result = await ats_call
                return plain, ats_result, {
                    "naturalness": 100,
                    "sameness": 0,
                    "skip_risk": [],
                    "flags": [],
                    "rewrite_instructions": "",
                    "skipped": True,
                }
            ats_result, voice_result = await asyncio.gather(
                ats_call,
                asyncio.to_thread(evaluate_voice, jd_text, plain, source),
            )
        return plain, ats_result, voice_result

    for attempt in range(1, max_attempts + 1):
        log.info("Tailor attempt %s/%s (freedom=%s)", attempt, max_attempts, freedom)
        writing = "Writing CV" if attempt == 1 else "Rewriting CV"
        if max_attempts > 1:
            writing = f"{writing} ({attempt}/{max_attempts}, L{freedom})"
        note(writing)
        with patch("pipeline.tailor.load_config", return_value=job_cfg):
            llm_output = await asyncio.to_thread(
                generate_tailored_materials,
                company,
                role,
                jd_text,
                feedback_history,
                jd_brief,
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
        if verify_mode == "enforce":
            try:
                from pipeline.verify import verify_application

                _draft_parsed = normalize_parsed(parse_tagged_output(llm_output, job_cfg), job_cfg)
                _ground = verify_application(
                    titles=[
                        {**_base, "tailored": _draft_parsed.get(f"{_base.get('prefix')}_TITLE") or ""}
                        for _base in _verify_titles_base
                    ],
                    resume_text=resume_plain_text(_draft_parsed, job_cfg),
                    source_text=source,
                    career_years=_verify_years,
                )
                _ungrounded = _ground["ungrounded_claims"]
            except Exception as exc:
                log.warning("Grounding check failed on attempt %s for %s: %s", attempt, company, exc)
                _ungrounded = []
            if _ungrounded:
                blocked_history.append(
                    {"llm_output": llm_output, "ungrounded": _ungrounded, "attempt": attempt}
                )
                details = "; ".join(
                    f"{item.get('claim')} ({item.get('reason')})" for item in _ungrounded[:6]
                )
                feedback_history += (
                    f"\nAttempt {attempt} REJECTED - UNGROUNDED NUMBERS:\n"
                    f"These figures appear in the draft but not in cv_master/bank: {details}\n"
                    "Replace each with a sourced figure or drop the claim. Do not invent replacements.\n"
                )
                note("Ungrounded numbers — rewriting")
                log.warning(
                    "Attempt %s for %s blocked by numeric grounding: %s",
                    attempt,
                    company,
                    details,
                )
                continue
        scoring = "Scoring ATS + voice"
        if max_attempts > 1:
            scoring = f"{scoring} ({attempt}/{max_attempts})"
        note(scoring)
        plain, eval_result, voice_result = await evaluate_draft(parsed)
        score = int(eval_result.get("score") or 0)
        honesty = int(eval_result.get("honesty") or 0)
        critique = eval_result.get("critique") or "No critique provided."
        naturalness = int(voice_result.get("naturalness") or 0)
        sameness = int(voice_result.get("sameness") or 0)
        voice_ok, voice_issues = voice_passes(voice_result, job_cfg)
        revised_for_voice = False
        if voice_result.get("failed"):
            log.warning(
                "Voice evaluation failed on attempt %s; skipping the voice gate for this attempt.",
                attempt,
            )

        if (
            voice_gate
            and not voice_ok
            and voice_rewrite_enabled(job_cfg)
            and not voice_revision_used
        ):
            voice_revision_used = True
            note("Editing voice")
            revised_output = await asyncio.to_thread(
                revise_for_voice,
                company,
                role,
                jd_text,
                llm_output,
                voice_result,
                jd_brief,
            )
            if revised_output.strip():
                revised_parsed = parse_tagged_output(revised_output, job_cfg)
                revised_valid, revised_errors = validate_tailored_output(
                    revised_parsed, job_cfg
                )
                if revised_valid:
                    revised_plain, revised_eval, revised_voice = await evaluate_draft(
                        revised_parsed
                    )
                    revised_score = int(revised_eval.get("score") or 0)
                    revised_honesty = int(revised_eval.get("honesty") or 0)
                    revised_naturalness = int(revised_voice.get("naturalness") or 0)
                    revised_voice_ok, revised_issues = voice_passes(
                        revised_voice, job_cfg
                    )
                    accepted_revision = (
                        revised_voice_ok
                        and revised_score >= threshold
                        and revised_honesty >= min_honesty
                    ) or (
                        revised_naturalness > naturalness + 5
                        and revised_score >= score - 3
                        and revised_honesty >= min_honesty
                    )
                    if accepted_revision:
                        llm_output = revised_output
                        parsed = revised_parsed
                        plain = revised_plain
                        eval_result = revised_eval
                        voice_result = revised_voice
                        score = revised_score
                        honesty = revised_honesty
                        naturalness = revised_naturalness
                        sameness = int(revised_voice.get("sameness") or 0)
                        critique = eval_result.get("critique") or critique
                        voice_ok = revised_voice_ok
                        voice_issues = revised_issues
                        revised_for_voice = True
                        log.info(
                            "Accepted voice revision (voice %s, ATS %s, honesty %s)",
                            naturalness,
                            score,
                            honesty,
                        )
                else:
                    log.warning(
                        "Voice revision incomplete: %s", "; ".join(revised_errors)
                    )
            else:
                log.warning("Voice revision returned empty output")

        eval_result["fabrication_freedom"] = freedom
        eval_result["fabrication_freedom_mode"] = choice.get("mode")
        eval_result["fabrication_freedom_reason"] = choice.get("reason")
        eval_result["voice"] = voice_result
        eval_result["voice_score"] = naturalness
        eval_result["sameness"] = sameness
        eval_result["voice_issues"] = voice_issues

        passed = bool(
            score >= threshold
            and honesty >= min_honesty
            and (not voice_gate or voice_ok)
        )
        attempt_record = {
            "attempt": attempt,
            "freedom": freedom,
            "score": score,
            "honesty": honesty,
            "min_honesty": min_honesty,
            "threshold": threshold,
            "keyword_coverage": int(eval_result.get("keyword_coverage") or score),
            "voice_score": naturalness,
            "sameness": sameness,
            "voice_flags": list(voice_result.get("flags") or []),
            "voice_skip_risk": list(voice_result.get("skip_risk") or []),
            "voice_issues": voice_issues,
            "revised_for_voice": revised_for_voice,
            "critique": critique,
            "gaps": list(eval_result.get("gaps") or []),
            "passed": passed,
            "action": "accepted" if passed else "",
        }
        attempts_history.append(attempt_record)

        log.info(
            "Score %s/100 (honesty %s/100, voice %s/100, freedom %s, passed=%s)",
            score,
            honesty,
            naturalness,
            freedom,
            passed,
        )
        log.info("Critique: %s", critique)
        if voice_gate and not voice_ok:
            log.info("Voice issues: %s", "; ".join(voice_issues))

        # Winning attempt selection:
        # A passed attempt ALWAYS takes precedence over failing attempts.
        # If no attempt passes, retain the best composite quality attempt.
        if passed:
            if not has_passed or score > winning_eval.get("score", -1) or (
                score == winning_eval.get("score", -1)
                and naturalness > int(winning_eval.get("voice_score") or 0)
            ):
                has_passed = True
                winning_output = llm_output
                winning_eval = dict(eval_result)
                winning_cfg = job_cfg
                winning_freedom = freedom
            log.info(
                "Threshold reached: score %s>=%s, honesty %s>=%s, voice %s>=%s (freedom %s).",
                score,
                threshold,
                honesty,
                min_honesty,
                naturalness,
                voice_floor,
                freedom,
            )
            break
        else:
            if not has_passed:
                current_rank = (
                    draft_quality(eval_result, voice_gate),
                    naturalness if voice_gate else 0,
                    score,
                )
                best_rank = (
                    draft_quality(winning_eval, voice_gate),
                    int(winning_eval.get("voice_score") or 0) if voice_gate else 0,
                    int(winning_eval.get("score") or 0),
                )
                if not winning_output or current_rank > best_rank:
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
            elif score < threshold and voice_ok:
                # Model was truthful and natural but missed keyword requirements -> increment
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
                if voice_gate and not voice_ok:
                    adaptation_reason = (
                        f"Voice low ({naturalness} < {voice_floor}) → maintain L{freedom}"
                    )
                    attempt_record["action"] = f"maintain_voice (L{freedom})"
                else:
                    adaptation_reason = f"Gates met but not passed → maintain L{freedom}"
                    attempt_record["action"] = f"maintain (L{freedom})"

            min_honesty = honesty_gate(freedom, threshold)
            job_cfg = config_with_freedom(cfg, freedom)
            source = source_of_truth_text(job_cfg)

        voice_line = (
            f"Voice: {naturalness}/100 (need >={voice_floor}), sameness {sameness}/100, "
            f"flags {voice_result.get('flags')}, skip risks {voice_result.get('skip_risk')}\n"
            f"Voice fixes: {voice_result.get('rewrite_instructions')}\n"
            if voice_gate
            else ""
        )
        log.warning(
            "Below gates (need score>=%s honesty>=%s%s at freedom %s). Refining.",
            threshold,
            min_honesty,
            f" voice>={voice_floor}" if voice_gate else "",
            freedom,
        )
        feedback_history += (
            f"\nAttempt {attempt} score={score} honesty={honesty} freedom={attempt_record['freedom']} "
            f"(need score>={threshold}, honesty>={attempt_record['min_honesty']})\n"
            f"Adaptation: {adaptation_reason or 'Refining attempt'}.\n"
            f"Freedom for next attempt: L{freedom} ({level_label(freedom)}).\n"
            f"Critique: {critique}\n"
            f"Gaps: {eval_result.get('gaps')}\n"
            f"{voice_line}"
            f"Also protect the {job_cfg.cv_pages}-page budget — drop lower-value bullets if needed.\n"
        )

    if not winning_output and blocked_history:
        # Every attempt was blocked by numeric grounding: save the last draft
        # as an explicit failed-gates package for manual review rather than
        # losing the run silently. Blocking applies while alternatives exist;
        # exhaustion falls back to an annotated save, never a quiet pass.
        _last = blocked_history[-1]
        winning_output = _last["llm_output"]
        winning_eval = {
            "score": 0,
            "honesty": 0,
            "critique": "All tailor attempts were blocked by numeric grounding; saved last draft for manual review.",
            "gaps": [f"{item.get('claim')} — {item.get('reason')}" for item in _last["ungrounded"]],
            "blocked_by_grounding": True,
            "passed": False,
        }
        log.warning(
            "All %s attempt(s) for %s blocked by numeric grounding; saving annotated fallback.",
            len(blocked_history),
            company,
        )

    if not winning_output:
        log.error("No usable output for %s — skipping save.", company)
        return None

    winning_eval["attempts"] = attempts_history
    winning_eval["fabrication_freedom"] = winning_freedom
    winning_eval["passed"] = has_passed
    try:
        from pipeline.evaluation import enrich_evaluation

        winning_eval = enrich_evaluation(winning_eval, job, cfg)
    except Exception as exc:
        log.warning("Fit-score enrichment failed for %s: %s", company, exc)
    try:
        verify_on = bool(cfg.get("verification.enabled", True))
    except Exception:
        verify_on = True
    if verify_on and winning_output:
        try:
            from pipeline.tailor import (
                job_blocks as _job_blocks,
                normalize_parsed as _normalize_parsed,
                parse_tagged_output as _parse_output,
                resume_plain_text as _resume_text,
                source_of_truth_text as _source_text,
            )
            from pipeline.verify import verify_application

            _parsed_win = _normalize_parsed(_parse_output(winning_output, winning_cfg), winning_cfg)
            _career_years = None
            try:
                _career_years = float(winning_cfg.get("career.years_experience") or 0) or None
            except (TypeError, ValueError):
                _career_years = None
            _verification = verify_application(
                titles=[
                    {
                        "prefix": _job.get("prefix"),
                        "employer": _job.get("employer"),
                        "canonical": _job.get("default_title"),
                        "tailored": _parsed_win.get(f"{_job.get('prefix')}_TITLE") or "",
                    }
                    for _job in _job_blocks(winning_cfg)
                ],
                resume_text=_resume_text(_parsed_win, winning_cfg),
                source_text=_source_text(winning_cfg),
                career_years=_career_years,
            )
            winning_eval["title_warnings"] = _verification["title_warnings"]
            winning_eval["ungrounded_claims"] = _verification["ungrounded_claims"]
            winning_eval["claim_reviews"] = _verification["claim_reviews"]
            winning_eval["claims_supported"] = _verification["claims_supported"]
            winning_eval["verification_skipped"] = _verification["verification_skipped"]
        except Exception as exc:
            log.warning("Advisory verification failed for %s: %s", company, exc)
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
            jd_brief=jd_brief,
            on_progress=on_progress,
        )

    pdf_path = output_dir / f"{winning_cfg.cv_stem}.pdf"
    cl_path = output_dir / "cover_letter.md"
    why_path = output_dir / "why_i_fit.txt"
    playbook = render_playbook(winning_cfg, job, output_dir, pdf_path, cl_path, why_path)
    (output_dir / "playbook.md").write_text(playbook)

    machine_summary = str(winning_eval.get("machine_summary") or "")
    try:
        from pipeline.verify import verification_note_lines

        verify_lines = verification_note_lines(winning_eval)
    except Exception:
        verify_lines = []
    if machine_summary or verify_lines:
        analysis_path = output_dir / "analysis.md"
        try:
            existing = analysis_path.read_text() if analysis_path.exists() else ""
            appendix = ""
            if verify_lines:
                appendix += "\n\n## Verification (advisory — nothing here blocked the save)\n" + "\n".join(verify_lines)
            if machine_summary and "## Machine Summary" not in existing:
                appendix += "\n\n" + machine_summary
            if appendix.strip():
                analysis_path.write_text((existing.rstrip() + "\n" + appendix).strip() + "\n")
        except OSError as exc:
            log.warning("Could not append machine summary for %s: %s", company, exc)

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
        "--force",
        action="store_true",
        help="Force overwrite of existing packages for the same company and role.",
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
            await process_job(job, fill_form=args.fill_form, force=args.force)

    await asyncio.gather(*(_one(job) for job in jobs))
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
