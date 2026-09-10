#!/usr/bin/env python3
"""Benchmark the complete pipeline end-to-end with 3 representative job postings:
- OpenTable (Solutions Engineer / Forward Deployed Engineer)
- DualEntry (Senior/Staff Backend Engineer)
- Cohere (Software Engineer, Integrations)

Evaluates:
- Auto fabrication freedom level selection
- LLM generation quality & tag adherence
- ATS score and honesty evaluation
- Dynamic adaptation loop across retry attempts
- Resulting application package artifacts
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import load_config
from pipeline.fabrication import choose_fabrication_freedom, level_label
from pipeline.run_pipeline import process_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("benchmark-pipeline")

JOBS = [
    {
        "company": "OpenTable",
        "role": "Solutions Engineer / Forward Deployed Engineer",
        "url": "https://benchmarks.local/opentable-fde-solutions-engineer",
        "channel": "benchmark",
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
        "company": "DualEntry",
        "role": "Senior/Staff Backend Engineer",
        "url": "https://benchmarks.local/dualentry-senior-staff-backend-engineer",
        "channel": "benchmark",
        "jd": """
Company: DualEntry
Role: Senior/Staff Backend Engineer
Location: Canada (Remote)
Stage: Startup ($100M+ raised)

About DualEntry:
DualEntry is building the AI-native ERP system — the "OS for finance." We help mid-market 
businesses replace legacy accounting software with a real-time, AI-powered financial 
intelligence platform. We move incredibly fast, deploy daily, and value extreme ownership 
over process.

Role Overview:
We are looking for a Senior or Staff Backend Engineer who thrives in a fast-moving, 
high-trust environment. You will own entire features end-to-end, ship production code daily, 
and integrate with external financial systems (banks, fintech APIs).

Requirements:
- 5+ years of backend engineering experience
- Expert-level Python (FastAPI, Flask, or Django)
- Strong database skills: PostgreSQL, schema design, complex SQL queries
- Experience building complex business logic in financial or mission-critical domains
- Production-readiness: CI/CD, AWS, Docker, observability
- High agency: you write specs, scope work, and ship without being told what to do
- Experience with async Python, background jobs, event-driven systems
- REST API design and integration with third-party APIs (banking/fintech a plus)

Nice to have:
- Experience with accounting systems, ERP, or fintech
- Background in startups or high-growth environments
- Familiarity with AI/LLM integration in product features
""".strip(),
    },
    {
        "company": "Cohere",
        "role": "Software Engineer, Integrations",
        "url": "https://benchmarks.local/cohere-swe-integrations",
        "channel": "benchmark",
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


async def run_benchmark():
    cfg = load_config(force=True)
    out_records = []
    t_start_total = time.time()

    print("\n" + "=" * 110)
    print("STARTING END-TO-END PIPELINE BENCHMARK (3 JOBS: OpenTable, DualEntry, Cohere)")
    print(f"Config: provider={cfg.get('pipeline.provider')} | model={cfg.get('pipeline.model')} | ats_threshold={cfg.get('pipeline.ats_threshold')}")
    print(f"Auto Freedom: {cfg.get('pipeline.fabrication_freedom')} (max={cfg.get('pipeline.fabrication_freedom_max')}) | Cover letter: {cfg.get('pipeline.generate_cover_letter')}")
    print("=" * 110 + "\n")

    for idx, job in enumerate(JOBS, 1):
        company = job["company"]
        role = job["role"]
        print(f"\n[{idx}/{len(JOBS)}] === Processing {company} — {role} ===")
        t0 = time.time()

        # Check initial auto freedom decision
        initial_choice = choose_fabrication_freedom(cfg, job["jd"], job)
        print(f"  → Initial Auto Freedom Choice: L{initial_choice['level']} ({initial_choice['mode']}) - {initial_choice['reason']}")

        def on_progress(step: str):
            print(f"    [{company}] {step}...", flush=True)

        package_dir = await process_job(job, cfg=cfg, on_progress=on_progress)
        elapsed = round(time.time() - t0, 2)

        if not package_dir or not package_dir.exists():
            print(f"  ❌ FAILED to produce package for {company} in {elapsed}s")
            out_records.append({
                "company": company,
                "role": role,
                "success": False,
                "elapsed_seconds": elapsed,
                "initial_choice": initial_choice,
            })
            continue

        eval_json_path = package_dir / "evaluation.json"
        eval_data = {}
        if eval_json_path.exists():
            try:
                eval_data = json.loads(eval_json_path.read_text())
            except Exception as e:
                log.warning("Could not parse evaluation.json: %s", e)

        pdf_exists = (package_dir / f"{cfg.cv_stem}.pdf").exists()
        html_exists = (package_dir / f"{cfg.cv_stem}.html").exists()
        changes_exists = (package_dir / f"{cfg.cv_stem}_changes.md").exists()
        playbook_exists = (package_dir / "playbook.md").exists()
        cl_exists = (package_dir / "cover_letter.md").exists()

        winning_freedom = eval_data.get("fabrication_freedom", initial_choice["level"])
        score = eval_data.get("score")
        honesty = eval_data.get("honesty")
        attempts = eval_data.get("attempts", [])

        print(f"  ✅ SUCCESS: {company} finished in {elapsed}s")
        print(f"     Winning Freedom: L{winning_freedom} ({level_label(winning_freedom)})")
        print(f"     ATS Score: {score}/100 | Honesty: {honesty}/100 | Attempts: {len(attempts)}")
        print(f"     Output Folder: {package_dir.name}")
        print(f"     Artifacts: PDF={pdf_exists}, HTML={html_exists}, Changes={changes_exists}, Playbook={playbook_exists}, CoverLetter={cl_exists}")

        out_records.append({
            "company": company,
            "role": role,
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
            "package_dir": str(package_dir),
            "artifacts": {
                "pdf": pdf_exists,
                "html": html_exists,
                "changes": changes_exists,
                "playbook": playbook_exists,
                "cover_letter": cl_exists,
            }
        })

    total_time = round(time.time() - t_start_total, 2)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = ROOT / "benchmarks" / f"pipeline_benchmark_{timestamp}.json"
    out_file.write_text(json.dumps(out_records, indent=2))

    print("\n" + "=" * 110)
    print(f"PIPELINE BENCHMARK COMPLETED IN {total_time}s")
    print(f"Saved results: {out_file}")
    print("-" * 110)
    print(f"{'Company':<12} {'Role':<35} {'Init L':<7} {'Win L':<7} {'ATS':>4} {'Hon':>4} {'Att':>4} {'Time':>7} {'Status'}")
    print("-" * 110)
    for r in out_records:
        if not r.get("success"):
            print(f"{r['company']:<12} {r['role'][:34]:<35} {r.get('initial_freedom','-'):<7} {'-':<7} {'-':>4} {'-':>4} {'-':>4} {r['elapsed_seconds']:>6.1f}s ❌ FAIL")
        else:
            print(
                f"{r['company']:<12} {r['role'][:34]:<35} "
                f"L{r['initial_freedom']:<6} L{r['winning_freedom']:<6} "
                f"{r['ats_score']:>4} {r['honesty_score']:>4} {r['attempts_count']:>4} "
                f"{r['elapsed_seconds']:>6.1f}s ✅ OK"
            )
    print("=" * 110 + "\n")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
