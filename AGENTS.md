# AGENTS.md — job_search

## Invariants (do not break)
- **Never clicks Submit.** Autofill / browser automation halt before submission. `pipeline.auto_apply` stays `false`; `--fill-form` still never submits.
- **No hallucinated experience.** Tailor may only rephrase `cv_master.md` + `experience-bank/*.md`. Never invent employers, dates, degrees, metrics. `memory/feedback.md` writing rules load into every tailor run — read before editing prompts.
- **Never commit personal files:** `config.yaml` (may hold LinkedIn password), `cv_master.md`, `jobs.yaml`, `applied.yaml`, `deleted.yaml`, `.env`, `.camoufox-profile/`, `resumes/template.html`, `memory/*` (except templates), `experience-bank/*` (except examples/README), `applications/*` (except `.gitkeep`). Tracked counterparts are `*.example.*` / `*.template.md`. `applications/_tracker.md` is runtime-generated.

## Run everything in Docker
- Desk UI: `docker compose up ui --build` → http://127.0.0.1:8000. **Do not run uvicorn on the host.**
- CLI: `docker compose run --rm pipeline python3 -m pipeline.run_pipeline --hunt` or `--job <CompanySubstring>` (edit `jobs.yaml` first; `--force` to overwrite an existing package).
- `build.sh` is just `python3 -m pipeline.run_pipeline "$@"`.
- Python changes need container restart; HTML/CSS/JS under `web/` hot-reload via bind mount. After `requirements.txt`/Dockerfile changes use `--build`.
- Compose reads `.env` (`OPENCODE_API_KEY` primary when `pipeline.provider: opencode`; `NVIDIA_API_KEY`, else `GEMINI_API_KEY` or host `~/.gemini` for agy backup). LLM cascade: Muse Spark (Zen Responses API) → Zen chat fallbacks → NVIDIA chain (Nemotron 3 Ultra → DeepSeek V4 Flash → Gemma 4) → agy/Gemini.

## Tests (no LLM, no browser)
- Host (Python 3.10+): `python3 -m unittest discover -p 'test_*.py'`
- Docker: `docker compose run --rm --profile batch pipeline python3 -m unittest discover -p 'test_*.py'`
- LLM-calling runs take minutes; tests must stay hermetic.

## Config loading (pipeline/config.py)
- `config.example.yaml` = tracked defaults; gitignored `config.yaml` = overlay, **deep-merged**. A list/scalar set in `config.yaml` **replaces** the example (lists are not appended).
- Never write `key: []` followed by list items — invalid YAML, hunt fails with `Invalid YAML in config.example.yaml`.
- Paths (`cv_master.md`, etc.) resolve from `JOB_SEARCH_ROOT` (`/app` in Docker) or repo dir. `workspace.root` is for Cursor/agent skills only, ignored by Python in Docker.
- Host absolute paths in `config.yaml` break inside Docker (`Config.path` falls back to basename).

## Key code paths
- Entrypoints: `web/app.py` (FastAPI + SSE `/api/runs/{run_id}/stream`), `pipeline/run_pipeline.py` (`--hunt`, `--job`, `--force`, `--fill-form`, `--cover-letter/--no-cover-letter`).
- Hunt: `pipeline/hunt.py` + `browser_hunt.py` + `stack_match.py` (title vetoes + JD stack gates; `hunt.max_jobs: 0` = tailor every match, not top-N).
- Tailor loop: `pipeline/tailor.py` (rewrite → ATS/honesty critic → up to `pipeline.max_attempts`, default 3); freedom auto-escalates/de-escalates in `pipeline/fabrication.py`.
- Export: `pipeline/cv_export.py` → Playwright Chromium HTML→PDF. `experience.jobs[].prefix` must match `{{PREFIX_*}}` placeholders in `resumes/template.html`; adding a JOBn slot requires editing **both** `config.yaml` and the HTML template.
- Pages `.pages` output needs host helper: `python3 scripts/macos_pages_helper.py` (macOS + Pages.app).
- Output: `applications/<company>-<role>-<YYYY-MM-DD>/` (CV pdf/html/docx/pages, cover_letter.md, linkedin_dm.txt, why_i_fit.txt, playbook.md, analysis.md, llm_output_raw.txt).

## Camoufox / noVNC gotchas
- Headed Camoufox on `DISPLAY=:99` via `scripts/docker-entrypoint.sh` (`setsid` keeps Xvfb alive). Never use `headless: "virtual"` — invisible to the noVNC panel.
- Panel at `127.0.0.1:6080` (localhost-only) appears only for sign-in/2FA/CAPTCHA; hunt waits `login_wait_seconds` (default 300s).
- Container requires `seccomp:unconfined` + `SYS_ADMIN` + `MOZ_DISABLE_CONTENT_SANDBOX=1` + `shm_size: 2gb` (already in `docker-compose.yml` — don't remove). `cannot open display: :99` → rebuild `docker compose up ui --build`; EPERM sandbox error → check seccomp flags.
- Sessions persist in gitignored `.camoufox-profile/`; deleting it forces re-login.

## Agent skills
- `.agents/skills/job-hunt/SKILL.md` (8-phase orchestrator: bootstrap→research→tailor→review→submit-walkthrough→track→outreach→learn) delegates per-role work to `cv-tailor`. Both read the same `config.yaml`/master/bank/`applications/` layout.
