"""Benchmark the 3 NVIDIA models for CV tailoring quality, completeness, and speed."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import dotenv

# Load environment variables
dotenv.load_dotenv()

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_config
from pipeline.cv_format import page_size
from pipeline.llm import _call_nvidia
from pipeline.tailor import (
    all_tags,
    build_critic_prompt,
    build_tailor_prompt,
    evaluate_ats_score,
    job_blocks,
    parse_tagged_output,
    resume_plain_text,
    source_of_truth_text,
    validate_tailored_output,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("benchmark")

MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "deepseek-ai/deepseek-v4-flash-0731",
    "google/gemma-4-31b-it",
]

TEST_JD = """
Role: Senior Software Engineer, Distributed Systems & Platform
Company: Stripe
Location: Remote (Canada)

About the Team:
Stripe builds financial infrastructure for the internet. Our Distributed Systems and Data Platform team designs and maintains the high-throughput, low-latency transaction processing and event routing infrastructure that processes billions of dollars in volume annually.

Responsibilities:
- Architect, build, and optimize large-scale distributed services and real-time streaming pipelines using Python and event architectures (Kafka, message queues).
- Drive sub-second SLAs, high availability (99.999%), and robust fault tolerance across multi-region services.
- Partner with product engineers and data scientists to build low-latency inference pipelines and operational event consumers.
- Build resilient observability, monitoring, and automated testing frameworks across microservices.
- Incorporate AI/LLM developer tooling and automation to streamline platform operations.

Requirements:
- 5+ years of experience in backend software engineering with focus on distributed systems or real-time data pipelines.
- Strong proficiency in Python and event-driven architectures (Kafka, RabbitMQ, or similar).
- Experience optimizing PySpark, SQL, and database performance under high-concurrency workloads.
- Strong grounding in distributed systems concepts: idempotency, consistency, partition tolerance, backpressure, and caching.
- Experience with Docker, Kubernetes, AWS/cloud environments, and CI/CD pipelines.
- Permanent resident or eligible to work in Canada.
""".strip()


def run_benchmark(models: list[str] | None = None):
    cfg = load_config()
    target_models = models or MODELS
    print("=" * 80, flush=True)
    print("STARTING NVIDIA MODELS BENCHMARK FOR CV TAILORING", flush=True)
    print(f"Models to evaluate: {target_models}", flush=True)
    print("=" * 80, flush=True)

    prompt = build_tailor_prompt(
        cfg,
        company="Stripe",
        role="Senior Software Engineer, Distributed Systems & Platform",
        jd_text=TEST_JD,
    )
    print(f"Tailoring prompt constructed: {len(prompt)} characters\n", flush=True)

    results = []
    expected_tags = all_tags(cfg)

    for idx, model in enumerate(target_models, 1):
        print(f"\n[{idx}/{len(target_models)}] Testing model: {model}", flush=True)
        print("-" * 60, flush=True)
        t0 = time.time()
        res_entry = {
            "model": model,
            "success": False,
            "latency_seconds": 0.0,
            "output_chars": 0,
            "output_words": 0,
            "tag_count_present": 0,
            "tag_count_total": len(expected_tags),
            "missing_tags": [],
            "validation_passed": False,
            "validation_errors": [],
            "bullet_counts": {},
            "cover_letter_words": 0,
            "linkedin_dm_words": 0,
            "why_i_fit_words": 0,
            "ats_score": None,
            "ats_critique": "",
            "raw_output": "",
            "error": None,
        }

        try:
            raw_text = _call_nvidia(prompt, cfg, timeout=600, effort="high", model=model)
            dt = time.time() - t0
            res_entry["latency_seconds"] = round(dt, 2)
            res_entry["raw_output"] = raw_text
            res_entry["output_chars"] = len(raw_text)
            res_entry["output_words"] = len(raw_text.split())
            res_entry["success"] = True

            parsed = parse_tagged_output(raw_text, cfg)
            present_tags = [t for t in expected_tags if parsed.get(t)]
            missing_tags = [t for t in expected_tags if not parsed.get(t)]
            res_entry["tag_count_present"] = len(present_tags)
            res_entry["missing_tags"] = missing_tags

            # Employer bullets breakdown
            for job in job_blocks(cfg):
                prefix = job["prefix"]
                bullets = [
                    parsed.get(f"{prefix}_B{i}", "").strip()
                    for i in range(1, job["bullets"] + 1)
                    if parsed.get(f"{prefix}_B{i}", "").strip()
                ]
                res_entry["bullet_counts"][job["employer"]] = len(bullets)

            # Text sections
            res_entry["cover_letter_words"] = len((parsed.get("COVER_LETTER") or "").split())
            res_entry["linkedin_dm_words"] = len((parsed.get("LINKEDIN_DM") or "").split())
            res_entry["why_i_fit_words"] = len((parsed.get("WHY_I_FIT") or "").split())

            # Validation
            val_ok, val_errs = validate_tailored_output(parsed, cfg)
            res_entry["validation_passed"] = val_ok
            res_entry["validation_errors"] = val_errs

            print(f"  Execution time: {dt:.1f}s", flush=True)
            print(f"  Output length: {len(raw_text)} chars ({len(raw_text.split())} words)", flush=True)
            print(f"  Tags present: {len(present_tags)}/{len(expected_tags)}", flush=True)
            print(f"  Validation passed: {val_ok}", flush=True)
            if val_errs:
                print(f"  Validation issues ({len(val_errs)}):", flush=True)
                for err in val_errs:
                    print(f"    - {err}", flush=True)

            print(f"  Bullets: {res_entry['bullet_counts']}", flush=True)
            print(f"  Cover letter: {res_entry['cover_letter_words']} words", flush=True)
            print(f"  LinkedIn DM: {res_entry['linkedin_dm_words']} words", flush=True)

            # ATS evaluation
            try:
                cv_text = resume_plain_text(parsed, cfg)
                ats = evaluate_ats_score(TEST_JD, cv_text, source_of_truth_text(cfg))
                res_entry["ats_score"] = ats.get("score")
                res_entry["ats_honesty"] = ats.get("honesty")
                res_entry["ats_critique"] = ats.get("critique", "")
                print(f"  ATS Score: {ats.get('score')} (Honesty: {ats.get('honesty')})", flush=True)
            except Exception as e_ats:
                print(f"  ATS evaluation error: {e_ats}", flush=True)

        except Exception as exc:
            dt = time.time() - t0
            res_entry["latency_seconds"] = round(dt, 2)
            res_entry["error"] = str(exc)
            print(f"  FAILED in {dt:.1f}s: {exc}", flush=True)

        results.append(res_entry)

    # Save benchmark raw data
    out_dir = Path("benchmarks")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"benchmark_{int(time.time())}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80, flush=True)
    print("BENCHMARK SUMMARY", flush=True)
    print("=" * 80, flush=True)
    print(f"{'Model':<40} | {'Time':<6} | {'Valid?':<6} | {'Tags':<7} | {'ATS':<5} | {'Issues'}", flush=True)
    print("-" * 80, flush=True)
    for r in results:
        m_short = r["model"].split("/")[-1]
        t_str = f"{r['latency_seconds']:.1f}s"
        v_str = "PASS" if r["validation_passed"] else "FAIL"
        tag_str = f"{r['tag_count_present']}/{r['tag_count_total']}"
        ats_str = str(r["ats_score"]) if r["ats_score"] is not None else "N/A"
        issues_count = len(r["validation_errors"])
        print(f"{m_short:<40} | {t_str:<6} | {v_str:<6} | {tag_str:<7} | {ats_str:<5} | {issues_count} issues", flush=True)

    print("=" * 80, flush=True)
    print(f"Detailed results saved to {out_file}", flush=True)


if __name__ == "__main__":
    selected_models = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or None
    run_benchmark(models=selected_models)
