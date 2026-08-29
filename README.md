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
git clone git@github.com:YOUR_USER/job_hunter.git
cd job_hunter

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
cp cv_master.example.md cv_master.md
cp resumes/template.example.html resumes/template.html
cp memory/project.template.md memory/project.md
cp memory/feedback.template.md memory/feedback.md
```

Edit these:

| File | What to put in it |
|---|---|
| `config.yaml` | Name, contact, visa, and **`cv_format`** (pages, keep-together, fonts, alignment, section order) |
| `cv_master.md` | Canonical resume (gitignored — start from `cv_master.example.md`) |
| `resumes/template.html` | Your CV layout (gitignored — start from `resumes/template.example.html`) |
| `jobs.yaml` | Jobs to tailor (gitignored — start from `jobs.example.yaml`) |

`config.yaml`, `memory/*`, `experience-bank/*`, `applications/*`, and most of `resumes/` are gitignored so personal data stays local.

## 3. Add a job

Edit `jobs.yaml`. Paste the **full JD text**. LinkedIn URLs are not scraped.

```yaml
jobs:
  - company: ExampleCorp
    role: Senior Software Engineer
    location: Remote, Canada
    url: https://boards.greenhouse.io/examplecorp/jobs/123
    jd: |
      Paste the full posting here...
```

`company` and `role` are required. `jd:` is required unless the `url` is a public ATS page (Greenhouse / Lever) that `requests` can fetch.

## 4. Desk UI (recommended)

Watch the tailor run, then open score, critique, resume PDF, cover letter, and playbook.

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 -m uvicorn web.app:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

1. Paste a job URL. Click **Read URL**. Greenhouse/Lever often load. LinkedIn will ask you to paste the description.
2. Confirm company and role. Click **Run tailor**.
3. The live strip shows fetch → tailor → score → PDF.
4. The left rail lists packages. Open one for feedback, resume, cover letter, playbook, and analysis.

Docker (UI only; does not batch-apply every job):

```bash
docker compose up ui --build
```

## 5. CLI run

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

**Docker batch** (every job in `jobs.yaml`):

```bash
docker compose --profile batch up pipeline
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
| `cv_format.keep_together` | Sections that must not split (`skills`, `education`). |
| `cv_format.section_order` | Body order after the header (`summary`, `experience`, `skills`, `education`, `projects`). |
| `cv_format.header_align` / `body_align` | `left`/`center` and `left`/`justify`. |
| `cv_format.density` | `compact` or `comfortable` spacing. |
| `cv_format.bullets.max_lines` | Tailor prompt cap per bullet. |
| `cv_format.color` / `type` | Ink, accent, fonts. Defaults in `config.example.yaml`. |
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
