# Job Search Pipeline

Turns a job description into a tailored CV PDF, cover letter, LinkedIn DM, and a paste-by-field playbook. It only rewrites experience that is already in your master CV and experience bank. **It never clicks Submit.**

```
jobs.yaml / hunt / pasted URL  →  tailor (agy / Gemini)  →  honesty critic  →  HTML → PDF
                                                                      ↓
                                         applications/<company>-<role>-<date>/
```

The desk, hunt, Camoufox, and tailor all run **inside Docker**. Do not start uvicorn on the host.

## Prerequisites

- Docker + Docker Compose
- LLM auth: **either** `agy auth login` on the host (compose mounts `~/.gemini`) **or** `GEMINI_API_KEY` in the environment (see `.env.example`)

## How configuration is loaded

1. `config.example.yaml` is the full default schema. It is tracked in git.
2. `config.yaml` is **your** overlay. It is gitignored. Copy the example and edit it.
3. The pipeline **deep-merges** example + overlay. A key you set in `config.yaml` wins. A **list** you set replaces the example list (it is not appended).
4. Paths such as `cv_master.md` are resolved from `JOB_SEARCH_ROOT` (Docker sets `/app`) or the repo directory. `workspace.root` is for Cursor skills, not for Python inside Docker.

Never commit `config.yaml`. It may contain your LinkedIn password.

## First-time setup

```bash
git clone git@github.com:YOUR_USER/job_hunter.git
cd job_hunter

cp config.example.yaml config.yaml
cp jobs.example.yaml jobs.yaml
cp cv_master.example.md cv_master.md
cp resumes/template.example.html resumes/template.html
cp memory/project.template.md memory/project.md
cp memory/feedback.template.md memory/feedback.md
cp experience-bank/example-project.md experience-bank/YOUR-EMPLOYER.md
cp experience-bank/about-variants.example.md experience-bank/about-variants.md
cp applications/_tracker.example.md applications/_tracker.md   # optional
cp .env.example .env                                           # optional; or use agy login
mkdir -p .cursor && cp .cursor/mcp.example.json .cursor/mcp.json   # optional Indeed MCP in Cursor
```

Then edit the copies. Minimum to get a PDF: `config.yaml` (name + `experience.jobs`), `cv_master.md`, and `resumes/template.html`. Hunt also needs `career.target_roles`, `career.target_markets`, and LinkedIn login if you hunt LinkedIn.

### Personal files (gitignored) and their examples

| You create / edit (gitignored) | Copy from (tracked) | Required? | What to configure |
|---|---|---|---|
| `config.yaml` | `config.example.yaml` | **Yes** | Identity, visa, CV layout, hunt filters, LinkedIn login. See [config.yaml keys](#configyaml-what-to-set) below. |
| `cv_master.md` | `cv_master.example.md` | **Yes** | Canonical resume. The tailor may rephrase this; it must not invent jobs or metrics. Keep employer names and bullet counts aligned with `experience.jobs` and the HTML template. |
| `resumes/template.html` | `resumes/template.example.html` | **Yes** | HTML layout and `{{JOB1_*}}` placeholders. Company names in the HTML are static; titles/bullets are filled per job. |
| `jobs.yaml` | `jobs.example.yaml` | For CLI | Queue of postings. The desk appends here when you tailor. Each job needs `company`, `role`, and `jd` (or a public ATS `url`). |
| `memory/project.md` | `memory/project.template.md` | Recommended | Profile narrative, visa wording, positioning. Loaded into the tailor prompt. Keep in sync with `config.yaml` `user` / `visa` / `career`. |
| `memory/feedback.md` | `memory/feedback.template.md` | Recommended | Writing rules. Add a rule whenever you correct a draft so the next tailor does not repeat it. |
| `experience-bank/*.md` | `experience-bank/example-project.md`, `about-variants.example.md` | Recommended | Alternate bullets per employer/project. Filenames with `example` are skipped by the tailor. |
| `applications/_tracker.md` | `applications/_tracker.example.md` | Optional | Status table. Created/appended automatically after a successful tailor. |
| `.env` | `.env.example` | If no agy login | `GEMINI_API_KEY=...`. Export it before Compose, or `set -a; source .env; set +a`. |
| `.cursor/mcp.json` | `.cursor/mcp.example.json` | Optional | Indeed MCP URL for Cursor. OAuth is in Cursor Settings → MCP. Desk hunt continues with Camoufox if OAuth is missing. |
| `.camoufox-profile/` | (created at runtime) | Auto | Browser cookies. Gitignored. Re-login if you delete it. |
| `cv_master.tex` | `cv_master.tex.example` | No | Legacy TeX master. The Docker pipeline uses HTML + Playwright (`cv_format.engine: html-playwright`). |

`applications/<company>-<role>-<date>/` is also gitignored. That is output, not input.

## config.yaml: what to set

Open `config.example.yaml` for every key and comment. Below is what people actually change, and **where** it lives.

### Identity and work rights — `user`, `visa`, `outreach`

| Key | Where | Why |
|---|---|---|
| `user.full_name` / `preferred_name` | PDF filename, header, letters | Must match how you apply. |
| `user.email`, `phone`, `city`, `country` | Contact line, playbook | Phone format follows `cv_format.phone_format`. |
| `user.linkedin`, `user.github` | Contact links | `cv_format.contact_links`: `labels` vs full URLs. |
| `visa.status`, `visa.description` | Cover letter, playbook | Be honest. Forms will ask. |
| `visa.may_need_future_sponsorship` | Playbook | Sponsorship questions. |
| `outreach.signoff` | LinkedIn DM | e.g. `— Jane`. |

### Career and hunt targeting — `career`, `hunt`

| Key | Where | Why |
|---|---|---|
| `career.stage` | Tone | `junior` \| `mid` \| `senior` \| `career-changer` \| `academic`. |
| `career.years_experience` | Hunt fit gate | Skip JDs that require more than this plus `hunt.years_buffer` (default 2). |
| `career.target_markets` | Hunt location | e.g. `Canada`. |
| `career.target_roles` | Hunt queries + title allowlist | Exact titles you want. Seniority in `hunt.exclude_levels` is skipped unless the same phrase is here. |
| `hunt.max_jobs` | Desk “Hunt from profile” | Cap per hunt. |
| `hunt.exclude_levels` | Title filter | e.g. intern, junior, staff, principal. |
| `hunt.exclude_title_tokens` | Title filter | e.g. manager, director. |
| `hunt.preferred_skills` | Ranking | Boost Python/Kafka/… if they appear in the JD. |
| `hunt.reject_skills` | JD filter | Drop roles that require a skill you will not use (example: `java`). |
| `hunt.exclude_companies` | Search **and** saved jobs | Current/former employers you will not apply to. |
| `hunt.saved_jobs` | LinkedIn/Indeed saved | Treated as matches (fit gates skipped) unless the company is excluded. |
| `hunt.sources` / `hunt.ats_boards` | Camoufox | LinkedIn, Indeed, Google ATS dorks, Greenhouse/Lever/Ashby boards. |
| `hunt.mcp.indeed` | Optional MCP | Same URL as `.cursor/mcp.example.json`. |
| `hunt.browser.logins.linkedin` | Auto-fill LinkedIn | **Password only in gitignored `config.yaml`.** Empty password = sign in by hand in the Camoufox session. Docker has no Mac window; login is on the virtual display. Cookies persist in `.camoufox-profile/`. |

### CV layout — `cv_format`, `experience`

| Key | Where | Why |
|---|---|---|
| `cv_format.pages` | PDF length | Source of truth. Type is not shrunk to fit (except a 1-page target that overflows). |
| `cv_format.page_size` | PDF | `letter` or `a4`. |
| `cv_format.density` | Type, leading, margins | `compact` or `comfortable`. |
| `cv_format.header_align` / `body_align` | Layout | `left`/`center` and `left`/`justify`. |
| `cv_format.keep_together` | Pagination | `skills`, `education` must not split. |
| `cv_format.section_order` | Body order | `summary`, `experience`, `skills`, `education`, `projects`. |
| `cv_format.bullets.max_lines` | Tailor prompt | Keep bullets short. |
| `cv_format.color` / `type` | HTML CSS tokens | Fonts and ink. |
| `experience.jobs` | HTML slots | `prefix` must match `{{JOB1_TITLE}}` etc. in `resumes/template.html`. `bullets` is how many lines the tailor fills. `employer` is the real company name. |

If you add a fourth job, add a `JOB4` block in **both** `config.yaml` and `resumes/template.html`.

### Pipeline runtime — `pipeline`

| Key | Why |
|---|---|
| `pipeline.model` | `agy` model (default `gemini-3.1-pro`). |
| `pipeline.ats_threshold` | Stop when score **and** honesty meet this (default 80). |
| `pipeline.max_attempts` | Tailor/critic loops (default 3). |
| `pipeline.auto_apply` | Leave `false`. `--fill-form` still never clicks Submit. |

## Desk UI (Docker)

```bash
# optional: export GEMINI_API_KEY from .env
docker compose up ui --build
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The table updates as each package is written. Open PDF shows the file in the browser (it does not download). Already tailored company/role or job URL combinations are skipped.

1. Click **Hunt from profile**. Camoufox runs in the container. LinkedIn may need a one-time sign-in in that session. Apply/Submit is never clicked.
2. The table lists **job name**, **company**, **resume** (PDF), and **job link**. Click a row for score, cover letter, and playbook.
3. Optional: expand **Add postings by URL**. Paste one or more job links (one per line). Paste the **job description** to tailor from that text (used with a single URL).

## CLI (Docker)

```bash
docker compose run --rm pipeline python3 -m pipeline.run_pipeline --hunt
docker compose run --rm pipeline python3 -m pipeline.run_pipeline --job ExampleCorp
```

A run calls the LLM (can take a few minutes). Edit `jobs.yaml` first for `--job`.

## Output

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

## Tests (no LLM)

```bash
python3 -m unittest pipeline.test_pipeline
```

## Cursor skills

Skills `job-hunt` and `cv-tailor` use the same `config.yaml`, master CV, bank, and `applications/` folders. Use the pipeline for PDFs; use the skills in chat to review drafts, research companies, and walk through submission. Indeed MCP: copy `.cursor/mcp.example.json` and complete OAuth in Cursor Settings → MCP.
