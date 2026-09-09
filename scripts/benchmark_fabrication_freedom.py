#!/usr/bin/env python3
"""Live LLM matrix: tailor + ATS evaluate once per fabrication freedom level 0–5.

Uses the current NVIDIA model chain (550B → DeepSeek → Gemma 4) and skips agy
so the run cannot hang overnight. Restores fabrication_freedom when finished.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

import dotenv

dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import Config, load_config
from pipeline.fabrication import honesty_gate, level_label
from pipeline.llm import _call_nvidia
from pipeline.tailor import (
    build_tailor_prompt,
    evaluate_ats_score,
    parse_tagged_output,
    resume_plain_text,
    source_of_truth_text,
    validate_tailored_output,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("freedom-live")

# Current NVIDIA chain: 550B → DeepSeek → Gemma 4 (matches config.yaml).
NVIDIA_CHAIN = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek-ai/deepseek-v4-flash-0731",
    "google/gemma-4-31b-it",
]
CALL_TIMEOUT = 600  # ultra can be slow; still skip agy so we don't hang overnight

# Mid-difficulty JD: strong Python/API overlap, but RAG/agents + some stack stretch.
TEST_JD = """
Role: Software Engineer, Integrations
Company: Cohere
Location: Remote - Canada (Toronto / Montreal OK)

About the role:
Build and maintain production integrations that connect Cohere's enterprise LLM platform to
customer systems — APIs, data pipelines, and internal tools. Partner with solutions and
customer teams to turn ambiguous integration needs into reliable, observable services.

Requirements:
- Strong production Python (FastAPI/Flask or similar) and REST API design
- Experience shipping backend services on AWS with Docker; CI/CD familiarity
- Comfortable working with customers or internal stakeholders on technical requirements
- Hands-on with LLM APIs, RAG, or agent-style workflows in production or serious prototypes
- Event-driven systems experience (Kafka, SQS, or similar)
- Bonus: TypeScript/Node, Terraform, Kubernetes, evaluation/observability for LLM apps
""".strip()

COMPANY = "Cohere"
ROLE = "Software Engineer, Integrations"

FAB_MARKERS = [
    r"\bPostGIS\b",
    r"\bRust\b",
    r"\bGolang\b",
    r"(?<![A-Za-z])Go(?![A-Za-z])",
    r"\bTypeScript\b",
    r"\bNext\.?js\b",
    r"\bGraphQL\b",
    r"\bTerraform\b",
    r"\bSnowflake\b",
    r"\bDatabricks\b",
    r"\bKubernetes\b",
    r"\bRAG\b",
    r"\bagentic\b",
    r"\bLangChain\b",
    r"\bLangGraph\b",
    r"\bStaff Engineer\b",
    r"\bStaff[- ]level\b",
    r"\bPrincipal\b",
    r"\b100,?000\+?\s+users\b",
    r"\bmillions of users\b",
    r"\$\d",
]


def marker_hits(text: str) -> list[str]:
    return [p for p in FAB_MARKERS if re.search(p, text or "", re.I)]


def config_for_level(base_cfg: Config, level: int) -> Config:
    data = copy.deepcopy(base_cfg.data)
    data.setdefault("pipeline", {})
    data["pipeline"]["fabrication_freedom"] = level
    return Config(data, base_cfg.root)


def complete_nvidia_chain(prompt: str, cfg: Config, *, effort: str) -> str:
    """NVIDIA-only completion in config order (550B → DeepSeek → Gemma 4); never falls back to agy."""
    from pipeline.llm import nvidia_model_chain

    chain = nvidia_model_chain(cfg) or NVIDIA_CHAIN
    # Ensure user-requested order if config drifts
    preferred = NVIDIA_CHAIN
    ordered = []
    for m in preferred:
        if m in chain and m not in ordered:
            ordered.append(m)
    for m in chain:
        if m not in ordered:
            ordered.append(m)

    last_err = None
    for model in ordered:
        try:
            log.info("Trying %s (%s)", model, effort)
            text = _call_nvidia(prompt, cfg, timeout=CALL_TIMEOUT, effort=effort, model=model)
            if text and text.strip():
                if "<TITLE>" in prompt and "<TITLE>" in text:
                    if not any(tag in text for tag in ("</ANALYSIS>", "</WHY_I_FIT>", "</COVER_LETTER>")):
                        log.warning("%s returned truncated tailor output — next model", model)
                        continue
                log.info("Using %s", model)
                return text
            log.warning("%s returned empty", model)
        except Exception as exc:
            last_err = exc
            log.warning("%s failed: %s", model, exc)
    raise RuntimeError(f"All NVIDIA chain models failed (last={last_err})")


def run_level(level: int, base_cfg: Config, out_dir: Path) -> dict:
    cfg = config_for_level(base_cfg, level)
    threshold = int(cfg.get("pipeline.ats_threshold", 80) or 80)
    gate = honesty_gate(level, threshold)
    source = source_of_truth_text(cfg)
    with patch("pipeline.tailor.load_config", return_value=cfg):
        prompt = build_tailor_prompt(cfg, COMPANY, ROLE, TEST_JD, feedback_history="")

    t0 = time.time()
    log.info("=== Freedom %s: generating tailor ===", level)
    raw = complete_nvidia_chain(prompt, cfg, effort="high")
    gen_s = round(time.time() - t0, 1)

    parsed = parse_tagged_output(raw, cfg)
    valid, errors = validate_tailored_output(parsed, cfg)
    plain = resume_plain_text(parsed, cfg)

    t1 = time.time()
    log.info("=== Freedom %s: evaluating ATS ===", level)
    with patch("pipeline.tailor.load_config", return_value=cfg), patch(
        "pipeline.tailor.complete_prompt",
        side_effect=lambda p, effort="low": complete_nvidia_chain(p, cfg, effort=effort),
    ):
        evaluation = evaluate_ats_score(TEST_JD, plain, source)
    eval_s = round(time.time() - t1, 1)

    score = int(evaluation.get("score") or 0)
    honesty = int(evaluation.get("honesty") or 0)
    accepted = score >= threshold and honesty >= gate

    level_dir = out_dir / f"freedom-{level}"
    level_dir.mkdir(parents=True, exist_ok=True)
    (level_dir / "llm_output_raw.txt").write_text(raw or "")
    (level_dir / "resume_plain.txt").write_text(plain or "")
    (level_dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2))
    summary = {
        "level": level,
        "label": level_label(level),
        "valid": valid,
        "validation_errors": errors,
        "score": score,
        "honesty": honesty,
        "keyword_coverage": evaluation.get("keyword_coverage"),
        "ats_threshold": threshold,
        "honesty_gate": gate,
        "accepted": accepted,
        "gen_seconds": gen_s,
        "eval_seconds": eval_s,
        "marker_hits": marker_hits(plain),
        "title": parsed.get("TITLE"),
        "summary": parsed.get("SUMMARY"),
        "skills_backend": parsed.get("SKILL_BACKEND"),
        "skills_cloud": parsed.get("SKILL_CLOUD"),
        "critique": evaluation.get("critique"),
        "gaps": evaluation.get("gaps"),
    }
    (level_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return {
        "level": level,
        "label": level_label(level),
        "valid": valid,
        "errors": errors,
        "score": score,
        "honesty": honesty,
        "keyword_coverage": evaluation.get("keyword_coverage"),
        "gate": gate,
        "accepted": accepted,
        "gen_s": gen_s,
        "eval_s": eval_s,
        "markers": marker_hits(plain),
        "title": (parsed.get("TITLE") or "")[:100],
        "skills_backend": (parsed.get("SKILL_BACKEND") or "")[:120],
        "critique": (evaluation.get("critique") or "")[:180],
    }


def main() -> int:
    cfg = load_config(force=True)
    out_dir = cfg.root / "benchmarks" / "fabrication-freedom-cohere"
    # fresh run
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for level in range(0, 6):
        try:
            row = run_level(level, cfg, out_dir)
            results.append(row)
            log.info(
                "L%s done valid=%s score=%s honesty=%s accepted=%s",
                level,
                row["valid"],
                row["score"],
                row["honesty"],
                row["accepted"],
            )
        except Exception as exc:
            log.exception("Freedom %s failed", level)
            results.append({"level": level, "error": str(exc), "accepted": False, "valid": False})

    (out_dir / "matrix.json").write_text(json.dumps(results, indent=2))

    print("\n" + "=" * 110)
    print(
        f"{'L':<3} {'Valid':<6} {'ATS':>4} {'Hon':>4} {'Gate':>4} {'OK':<6} "
        f"{'Gen':>6} {'Eval':>6}  Markers | Title"
    )
    print("-" * 110)
    for row in results:
        if row.get("error"):
            print(f"{row['level']:<3} ERROR {row['error'][:90]}")
            continue
        markers = ",".join(row.get("markers") or []) or "-"
        print(
            f"{row['level']:<3} {str(row['valid']):<6} {row['score']:>4} {row['honesty']:>4} "
            f"{row['gate']:>4} {str(row['accepted']):<6} {row['gen_s']:>5}s {row['eval_s']:>5}s  "
            f"{markers} | {row.get('title', '')}"
        )
    print("=" * 110)
    print(f"Artifacts: {out_dir}")

    # Fail the process if any level produced invalid materials (empty LLM output).
    return 0 if all(r.get("valid") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
