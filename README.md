# Job Hunter pipeline

Tailor a 1-page HTML/PDF CV, cover letter, LinkedIn DM, and a paste-by-field playbook for each job in `jobs.yaml`. An honesty-gated critic loop rewrites **existing** experience only. **You** click Submit.

## What it does
- Reads `config.yaml` for name, contact, visa, and paths (no hardcoded personal details in code).
- Pulls bullet variants from `experience-bank/` so tailoring is role-framed, not invented.
- Writes `applications/<company>-<role>-<date>/` with CV PDF, cover letter, DM, why-I-fit, analysis, playbook, and a changes log.
- Appends a **draft** row to `applications/_tracker.md`.
- Does **not** auto-apply. `--fill-form` is an optional Greenhouse/Lever screenshot helper and still never clicks Submit.

## Prerequisites
- Docker and Docker Compose, **or** Python 3.10+ with `agy` on your PATH
- Optional: `GEMINI_API_KEY`, or an existing `agy auth login` / `~/.gemini` session

## Setup

```bash
cp config.example.yaml config.yaml   # then edit name, contact, visa
cp jobs.example.yaml jobs.yaml       # then paste real JDs
```

Fill in:
- `memory/project.md` — profile and visa
- `memory/feedback.md` — writing guardrails
- `cv_master.md` — canonical resume
- `experience-bank/*.md` — per-job bullet variants by role type
- `resumes/template.html` — layout (placeholders filled by the pipeline)

Paste job descriptions into `jobs.yaml`. LinkedIn URLs are not scrapeable; the `jd:` field is required.

## Run

```bash
docker-compose build pipeline
docker-compose up
```

Or locally after `pip install -r requirements.txt` and `playwright install chromium`:

```bash
python3 -m pipeline.run_pipeline
python3 -m pipeline.run_pipeline --job Cohere
./build.sh --job Hootsuite
```

Tests (no LLM): `python3 -m unittest pipeline.test_pipeline`

## Output

For each job:

```
applications/<company>-<role>-<YYYY-MM-DD>/
├── <Name>_CV.html / .pdf
├── <Name>_CV_changes.md
├── cover_letter.md
├── linkedin_dm.txt
├── why_i_fit.txt
├── analysis.md
└── playbook.md          ← paste these fields into the ATS; you click Submit
```

## Cursor skills

`job-hunt` and `cv-tailor` follow the same files and rules. Prefer running the pipeline for PDFs; use the skills in chat to review drafts, research companies, and walk through submission.
