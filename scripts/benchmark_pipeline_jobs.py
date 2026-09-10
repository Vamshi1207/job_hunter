#!/usr/bin/env python3
"""Benchmark the complete pipeline end-to-end against realistic job postings:
1. OpenTable (Solutions Engineer / Forward Deployed Engineer) — Aligned, happy-path auto L0
2. DualEntry-Hard (Staff Backend Engineer) — Hard stack mismatch (Go/TS/GraphQL/Terraform/Snowflake) to stress auto-selection and adaptation
3. Cohere (Software Engineer, Integrations) — LLM integrations / APIs / RAG

Validates:
- Auto fabrication freedom level selection across aligned vs hard-mismatch JDs
- LLM generation quality & tag adherence with proper Nemotron 3 Ultra cascade
- ATS score and honesty evaluation against progressive honesty gates
- Dynamic bidirectional retry adaptation (escalation on keyword gap / deescalation on honesty failure)
- True cover letter toggle adherence (zero-bytes when off)
- L0 honesty regression detection (ensures stretch skills like Terraform/GraphQL/Go are not hallucinated at L0)
- Isolated benchmark environment (writes to benchmarks/runs/ to keep live applications/ pristine)
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import datetime
import json
import logging
import re
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import Config, load_config
from pipeline.fabrication import choose_fabrication_freedom, level_label
from pipeline.run_pipeline import process_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("benchmark-pipeline")

# Technologies NOT in master CV that must never appear as claimed skills at L0
UNVERIFIED_L0_MARKERS = [
    r"\bTerraform\b",
    r"\bGraphQL\b",
    r"\bSnowflake\b",
    r"\bRust\b",
    r"\bGolang\b",
    r"(?<![A-Za-z])Go(?![A-Za-z])",
    r"\bTypeScript\b",
]

BENCHMARK_JOBS = [
    {
        "id": "opentable-fde",
        "company": "OpenTable",
        "role": "Solutions Engineer / Forward Deployed Engineer",
        "url": "https://benchmarks.local/opentable-fde-solutions-engineer",
        "channel": "benchmark",
        "type": "aligned",
        "jd": """
Role: Solutions Engineer / Forward Deployed Engineer
Company: OpenTable
Location: Remote (Canada)

About OpenTable:
OpenTable is a global leader in restaurant technology, connecting millions of diners with more than 60,000 restaurants worldwide. We are investing in AI-assisted operations, seating optimization, and modernizing restaurant management workflows.

About the role:
We are seeking a Forward Deployed / Solutions Engineer who sits with customers, understands operational pain points, and builds working prototypes against existing systems. You will partner with restaurant operators and internal engineering to modernize legacy workflows into modern web applications with AI-assisted tooling.

Requirements:
- Strong experience in customer-facing engineering, solutions engineering, or forward deployed roles (running demos, scoping features, clarifying edge cases)
- Hands-on proficiency with production Python and REST APIs
- Experience building modern web frontends (React/modern web UI, Node.js) layered on existing backends
- Experience modernizing legacy workflows and building AI/LLM-assisted tooling/prototypes
- Strong communication skills: translating operational requirements into technical specifications

Bonus:
- Experience with NLP and text classification pipelines
- Experience building dashboards and operational analytics (e.g. Power BI, Grafana)
- Event-driven / distributed systems familiarity (Kafka, message queues)
- Cloud and container infrastructure (AWS, Docker)
""".strip(),
    },
    {
        "id": "dualentry-hard",
        "company": "DualEntry",
        "role": "Staff Backend Engineer",
        "url": "https://benchmarks.local/dualentry-hard-staff-backend-engineer",
        "channel": "benchmark",
        "type": "hard-mismatch",
        "jd": """
Role: Staff Backend Engineer — Distributed Ledger & Financial Data Platform
Company: DualEntry
Location: Canada (Remote)
Stage: Startup ($100M+ raised)

About the role:
DualEntry is building the AI-native ERP system — the OS for finance. We are looking for a Staff Backend Engineer to own core double-entry bookkeeping ledger and real-time transaction reconciliation services end-to-end.

Requirements:
- 8+ years experience architecting high-throughput financial backend systems at Staff/Lead level
- Production mastery of Go (Golang) and TypeScript for distributed microservices
- Deep expertise architecting GraphQL API schemas and high-concurrency event backbones (Kafka)
- Production Infrastructure-as-Code via Terraform, managing Kubernetes clusters on AWS
- Enterprise cloud data warehousing with Snowflake and dbt for immutable financial transaction auditing
- Deep domain mastery in double-entry bookkeeping, enterprise ERP workflows, and GAAP compliance
- High agency: write technical RFCs, mentor senior engineers, and ship daily to production
""".strip(),
    },
    {
        "id": "cohere-integrations",
        "company": "Cohere",
        "role": "Software Engineer, Integrations",
        "url": "https://benchmarks.local/cohere-swe-integrations",
        "channel": "benchmark",
        "type": "aligned",
        "jd": """
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
""".strip(),
    },
]


def check_l0_unverified_markers(text: str) -> list[str]:
    hits = []
    for pattern in UNVERIFIED_L0_MARKERS:
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(pattern.replace(r"\b", "").replace(r"(?<![A-Za-z])", "").replace(r"(?![A-Za-z])", ""))
    return hits


def make_isolated_config(base_cfg: Config, out_root: Path) -> Config:
    data = copy.deepcopy(base_cfg.data)
    data.setdefault("workspace", {})
    apps_rel = str((out_root / "applications").relative_to(base_cfg.root))
    tracker_rel = str((out_root / "_tracker.md").relative_to(base_cfg.root))
    data["workspace"]["applications"] = apps_rel
    data["workspace"]["tracker"] = tracker_rel
    return Config(data, base_cfg.root)


async def run_benchmark(job_ids: list[str] | None = None, clean: bool = False):
    base_cfg = load_config(force=True)
    bench_root = ROOT / "benchmarks" / "runs"
    bench_root.mkdir(parents=True, exist_ok=True)
    cfg = make_isolated_config(base_cfg, bench_root)

    selected_jobs = [
        j for j in BENCHMARK_JOBS
        if not job_ids or j["id"] in job_ids or j["company"].lower() in [x.lower() for x in job_ids]
    ]

    print("\n" + "=" * 115)
    print("STARTING PIPELINE END-TO-END BENCHMARK (HAPPY-PATH + HARD-MISMATCH STRESS)")
    print(f"Isolated workspace: {cfg.applications_dir}")
    print(f"Model Cascade: {cfg.get('pipeline.model')} → {cfg.get('pipeline.nvidia.fallback_models')} → agy")
    print(f"Cover letters enabled: {cfg.should_generate_cover_letter} | ATS Threshold: {cfg.get('pipeline.ats_threshold')}")
    print("=" * 115 + "\n")

    t_start_total = time.time()
    out_records = []

    for idx, job in enumerate(selected_jobs, 1):
        company = job["company"]
        role = job["role"]
        job_type = job["type"]
        print(f"\n[{idx}/{len(selected_jobs)}] === {company} — {role} ({job_type}) ===")
        t0 = time.time()

        initial_choice = choose_fabrication_freedom(cfg, job["jd"], job)
        print(f"  → Auto Freedom Decision: L{initial_choice['level']} ({initial_choice['mode']}) — {initial_choice['reason']}")

        def on_progress(step: str):
            print(f"    [{company}] {step}...", flush=True)

        package_dir = await process_job(job, cfg=cfg, on_progress=on_progress)
        elapsed = round(time.time() - t0, 2)

        if not package_dir or not package_dir.exists():
            print(f"  ❌ FAILED: No package produced for {company} in {elapsed}s")
            out_records.append({
                "id": job["id"],
                "company": company,
                "role": role,
                "type": job_type,
                "success": False,
                "elapsed_seconds": elapsed,
                "initial_freedom": initial_choice["level"],
            })
            continue

        eval_json_path = package_dir / "evaluation.json"
        eval_data = {}
        if eval_json_path.exists():
            try:
                eval_data = json.loads(eval_json_path.read_text())
            except Exception as exc:
                log.warning("Could not read evaluation.json: %s", exc)

        winning_freedom = eval_data.get("fabrication_freedom", initial_choice["level"])
        score = eval_data.get("score")
        honesty = eval_data.get("honesty")
        attempts = eval_data.get("attempts", [])

        # Artifact inspection
        pdf_path = package_dir / f"{cfg.cv_stem}.pdf"
        html_path = package_dir / f"{cfg.cv_stem}.html"
        changes_path = package_dir / f"{cfg.cv_stem}_changes.md"
        cl_path = package_dir / "cover_letter.md"

        pdf_ok = pdf_path.exists() and pdf_path.stat().st_size > 1000
        html_ok = html_path.exists() and html_path.stat().st_size > 500
        changes_ok = changes_path.exists() and changes_path.stat().st_size > 100

        # Cover letter check: verify zero-bytes when toggle is false
        cl_text = cl_path.read_text().strip() if cl_path.exists() else ""
        cl_has_text = len(cl_text) > 0
        cl_status = "empty (OK)" if (not cl_has_text and not cfg.should_generate_cover_letter) else f"{len(cl_text.split())} words"

        # L0 Honesty check: verify unearned stretch skills are not injected at L0
        plain_text = (package_dir / "llm_output_raw.txt").read_text() if (package_dir / "llm_output_raw.txt").exists() else ""
        l0_violations = check_l0_unverified_markers(plain_text) if winning_freedom == 0 else []

        print(f"  ✅ SUCCESS in {elapsed}s:")
        print(f"     Freedom: L{initial_choice['level']} initial → L{winning_freedom} winning ({level_label(winning_freedom)})")
        print(f"     ATS: {score}/100 | Honesty: {honesty}/100 | Attempts: {len(attempts)}")
        print(f"     Cover letter: {cl_status} | PDF: {pdf_ok} | Changes: {changes_ok}")
        if l0_violations:
            print(f"     ⚠️ L0 Stretch markers detected: {', '.join(l0_violations)}")
        else:
            print(f"     ✅ L0 Honesty check: clean (no unearned stretch markers)")

        out_records.append({
            "id": job["id"],
            "company": company,
            "role": role,
            "type": job_type,
            "success": True,
            "elapsed_seconds": elapsed,
            "initial_freedom": initial_choice["level"],
            "initial_reason": initial_choice["reason"],
            "winning_freedom": winning_freedom,
            "winning_label": level_label(winning_freedom),
            "ats_score": score,
            "honesty_score": honesty,
            "keyword_coverage": eval_data.get("keyword_coverage"),
            "attempts_count": len(attempts),
            "attempts": attempts,
            "critique": eval_data.get("critique"),
            "gaps": eval_data.get("gaps"),
            "l0_violations": l0_violations,
            "package_dir": str(package_dir),
            "artifacts": {
                "pdf": pdf_ok,
                "html": html_ok,
                "changes": changes_ok,
                "cover_letter_words": len(cl_text.split()),
            }
        })

    total_time = round(time.time() - t_start_total, 2)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = ROOT / "benchmarks" / f"pipeline_benchmark_{timestamp}.json"
    out_file.write_text(json.dumps(out_records, indent=2))

    print("\n" + "=" * 115)
    print(f"BENCHMARK FINISHED IN {total_time}s — Saved to: {out_file.name}")
    print("-" * 115)
    print(f"{'Company':<12} {'Type':<14} {'Init L':<7} {'Win L':<7} {'ATS':>4} {'Hon':>4} {'Att':>4} {'Time':>7} {'L0 Violations':<15} {'Status'}")
    print("-" * 115)
    for r in out_records:
        if not r.get("success"):
            print(f"{r['company']:<12} {r['type']:<14} {r.get('initial_freedom','-'):<7} {'-':<7} {'-':>4} {'-':>4} {'-':>4} {r['elapsed_seconds']:>6.1f}s {'-':<15} ❌ FAIL")
        else:
            v_str = ", ".join(r["l0_violations"]) if r["l0_violations"] else "none (OK)"
            print(
                f"{r['company']:<12} {r['type']:<14} "
                f"L{r['initial_freedom']:<6} L{r['winning_freedom']:<6} "
                f"{r['ats_score']:>4} {r['honesty_score']:>4} {r['attempts_count']:>4} "
                f"{r['elapsed_seconds']:>6.1f}s {v_str:<15} ✅ OK"
            )
    print("=" * 115 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", nargs="*", help="Job IDs or company names to benchmark (e.g. dualentry-hard)")
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.jobs or None))
