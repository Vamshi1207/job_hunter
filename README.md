# Job Search Pipeline

Turns a job description into a tailored CV PDF, cover letter, LinkedIn DM, and a paste-by-field playbook. It only rewrites experience that is already in your master CV and experience bank. **It never clicks Submit.**

```
jobs.yaml  →  tailor (agy / Gemini)  →  honesty critic  →  HTML → PDF
                                                      ↓
                         applications/<company>-<role>-<date>/
```

## Prerequisites

- Python 3.10+ (3.9 works for tests)
- [`agy`](https://antigravity.google/) on your `PATH` (`which agy` should succeed). Docker installs this for you.
- Auth: run `agy auth login` on the host, **or** set `GEMINI_API_KEY`

Optional: Docker + Docker Compose.

## 1. Clone and install

```bash
git clone git@github.com:Vamshi1207/job_hunter.git
cd job_hunter   # or job_search — whatever the local folder is named

python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Put `agy` on your PATH if needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 2. Personal files (gitignored)

```bash
cp config.example.yaml config.yaml
cp jobs.example.yaml jobs.yaml
cp memory/project.template.md memory/project.md
cp memory/feedback.template.md memory/feedback.md
```

Edit these:

| File | What to put in it |
|---|---|
| `config.yaml` | Name, email, phone, LinkedIn, GitHub, visa, **`cv_format.pages`** (1, 2, 3, …) |
| `cv_master.md` | Canonical resume (source of truth) |
| `memory/project.md` | Short profile + visa |
| `memory/feedback.md` | Writing guardrails (do not invent tech, honest titles, …) |
| `experience-bank/*.md` | Per-job bullet variants by role type (see `experience-bank/README.md`) |
| `resumes/template.html` | CV layout; placeholders like `{{SUMMARY}}` are filled by the pipeline |
| `jobs.yaml` | Jobs to tailor (see next section) |

`config.yaml`, `memory/*`, `experience-bank/*`, `applications/*`, and most of `resumes/` are gitignored so personal data stays local.

## 3. Add a job

Edit `jobs.yaml`. Paste the **full JD text**. LinkedIn URLs are not scraped.

```yaml
jobs:
  - company: Cohere
    role: Forward Deployed Engineer, Agentic Platform
    location: Ottawa, Canada
    url: https://www.linkedin.com/jobs/view/4423802476/
    jd: |
      Paste the full posting here...
```

`company` and `role` are required. `jd:` is required unless the `url` is a public ATS page (Greenhouse / Lever) that `requests` can fetch.

## 4. Run

From the repo root:

```bash
# One company (substring match on company name)
python3 -m pipeline.run_pipeline --job Cohere

# Every job in jobs.yaml
python3 -m pipeline.run_pipeline

# Same thing
./build.sh --job Cohere
```

A run calls the LLM (can take a few minutes). It retries up to `pipeline.max_attempts` until the score and honesty both meet `pipeline.ats_threshold` (default 80).

**Docker** (uses `~/.gemini` from the host if you already logged in). `up` processes **every** job in `jobs.yaml`:

```bash
docker compose build pipeline
docker compose up
```

Pass a filter by overriding the command:

```bash
docker compose run --rm pipeline python3 -m pipeline.run_pipeline --job Cohere
```

`--fill-form` tries a Greenhouse/Lever screenshot fill. It still **does not** click Submit. Prefer `playbook.md`.

## 5. Review output

```
applications/<company>-<role>-<YYYY-MM-DD>/
├── <Your_Name>_CV.pdf      ← upload this
├── <Your_Name>_CV.html
├── <Your_Name>_CV_changes.md
├── cover_letter.md
├── linkedin_dm.txt
├── why_i_fit.txt
├── analysis.md
├── playbook.md             ← paste into the ATS; you click Submit
└── llm_output_raw.txt
```

Tracker row (draft) is appended to `applications/_tracker.md`. Open the PDF, edit if needed, then apply yourself.

## Config people actually change

| Key | Meaning |
|---|---|
| `cv_format.pages` | Target PDF length. Any positive integer. Type is not shrunk to fit (except a 1-page target that overflows). |
| `pipeline.ats_threshold` | Stop retrying at this score **and** honesty (default 80). |
| `pipeline.max_attempts` | Tailor/critic loops (default 3). |
| `pipeline.model` | `agy` model (default `gemini-3.1-pro`). |
| `user.*` / `visa.*` | Contact line, playbook, cover-letter visa line. |

## Tests (no LLM)

```bash
python3 -m unittest pipeline.test_pipeline
```

## Cursor

Skills `job-hunt` and `cv-tailor` use the same `config.yaml`, master CV, bank, and output folders. Use the pipeline for PDFs; use the skills in chat to review drafts, research companies, and walk through submission.
