---
name: cv-tailor
description: Tactical sub-skill that takes one job description and produces a complete tailored application package — 1-page CV PDF, 250-400 word cover letter PDF, ≤60-word LinkedIn cold DM, 3-bullet "why I fit" summary. Triggered by user pasting a JD URL/text or saying "apply to this", or invoked by the job-hunt orchestrator skill in Phase 2. All outputs land under applications/<company>-<role>-<date>/. Reads writing rules from memory/feedback.md before generating.
---

# CV-Tailor (Generic)

You are tailoring application materials for one specific role. Always read the user's memory files first:

- `memory/project.md` — user profile + target market + eligibility (visa, location)
- `memory/feedback.md` — accumulated writing rules (what to do, what to avoid)

These encode hard rules that override your defaults.

## Workspace expectations

```
${WORKSPACE}/cv_master.tex             ← canonical 1-page CV (never modified by this skill)
${WORKSPACE}/build.sh                  ← Tectonic-based build script
${WORKSPACE}/experience-bank/*.md      ← per-project bullet variants by role type
${WORKSPACE}/templates/                ← cover_letter / linkedin_dm / why_i_fit templates
${WORKSPACE}/applications/<company>-<role>-<YYYY-MM-DD>/   ← output target
```

## When to invoke

Trigger when user:
- Pastes a JD URL (Ashby / Greenhouse / Lever / Workable / careers page)
- Pastes JD text
- Says "apply to this", "tailor for this role", "/cv-tailor"
- The orchestrator skill (job-hunt) routes Phase 2 here

## Required inputs

At minimum:
1. **JD source** — URL or pasted text
2. **Company name** (extract from JD if not given)
3. **Role title** (extract from JD)

Optional but useful:
- Application deadline (sort priority)
- Whether user has a referral / warm intro

If JD is a URL, use WebFetch to pull it. If WebFetch fails (LinkedIn / Workday often block), ask the user to paste the JD text.

## Step-by-step algorithm

### Step 1 — Analyse the JD

Write to `applications/<company>-<role>-<date>/analysis.md`:

```markdown
# JD Analysis

**Company:** ...
**Role:** ...
**Location:** ... (in-person / hybrid / remote?)
**Stage:** ... (seed / Series A-D / public)
**JD URL:** ...

## Role type (pick ONE primary, optional secondary)
- [ ] Growth / Marketing
- [ ] Product / APM / PM
- [ ] BizOps / Operations / Founder Associate / Chief of Staff
- [ ] Solutions Engineer / Customer-facing technical
- [ ] Engineering / Founding Engineer
- [ ] Data / Analyst
- [ ] Customer Success / Support
- [ ] Other: ___

## Top 5 keywords / required signals from JD

## What this employer cares about most (read between the lines)

## Visa / sponsorship language in JD

## User's strongest matching evidence
(pull 3-5 bullets from experience bank that match)

## User's weakest gaps for this JD
(honest list — informs cover-letter "why I'd grow into this" angle)

## Recommended angle for cover letter
(one sentence describing the narrative thread)

## CV tailoring decisions
- Tagline: ...
- About variant: ...
- Project bullets selected: ...
- Skills section reorder: ...
```

### Step 2 — Tailor the CV

1. Copy `cv_master.tex` → `applications/<company>-<role>-<date>/cv.tex`.
2. **Edit ONLY these blocks** (do not restructure):
   - **Tagline**: adjust to match the role (e.g. for Growth: `... | Content / Distribution / Growth | ...`).
   - **About paragraph**: rewrite using the variant from `memory/project.md` defaults or `experience-bank/about-variants.md` matching the role type. Tweak final sentence to name the role / company. Keep ≤4 lines.
   - **Project bullets**: for each project, swap in 1–3 bullets from `experience-bank/<project>.md` that match keywords from the JD. Keep total page = 1.
   - **Skills section**: reorder so technologies most relevant to the JD appear first.
3. Build with `cd ${WORKSPACE} && tectonic --chatter minimal applications/<company>-<role>-<date>/cv.tex` (or use `build.sh`).
4. **Verify 1 page**: `pdftotext -bbox-layout cv.pdf - | grep -c '<page'` — must equal 1. If 2, trim one bullet from the longest project section.

### Step 3 — Cover letter

Use `templates/cover_letter.template.md` or write fresh. Fill placeholders:
- `{{COMPANY}}`, `{{ROLE}}`, `{{HIRING_MANAGER_OR_TEAM}}` (use "Hiring team" if unknown)
- `{{HOOK}}` — opens with the most JD-relevant accomplishment, NOT a generic intro
- `{{WHY_THIS_COMPANY}}` — 2–3 sentences citing something concrete you can verify (a launch, blog post, feature, recent funding). **Never write generic praise.**
- `{{WHY_ME}}` — 3 bullets max, each starting with a verb, each tied to one JD keyword
- `{{CLOSE}}` — short, no "looking forward to hearing from you" filler

Length target: **250–400 words**. Cut ruthlessly.

Save as `applications/<company>-<role>-<date>/cover_letter.tex` (matches CV style) or `.md` if using pandoc. Build PDF with Tectonic or pandoc.

### Step 4 — LinkedIn cold DM

Use `templates/linkedin_dm.template.md`. **Hard rules:**
- ≤60 words
- Opens with one specific, verifiable thing about the company / founder (not "I love your mission")
- One sentence on user's strongest signal
- One sentence ask: "Could I send you my CV?" or "Open to a 15-min chat?"
- No emojis. No "hope this finds you well."

Save to `applications/<company>-<role>-<date>/linkedin_dm.txt`.

### Step 5 — "Why I fit" 3-bullet summary

Use `templates/why_i_fit.template.md`. Three bullets, each ≤25 words, each tied to a JD requirement. For pasting into application form free-text fields like "Why are you interested in this role?".

Save to `applications/<company>-<role>-<date>/why_i_fit.txt`.

### Step 6 — Hand off

Print to user:
- Output directory path
- 1-page CV preview command (`open <pdf>`)
- One-paragraph summary of choices made: which bullets selected, which About variant, what cover letter angle
- Anything ambiguous in the JD that needs user confirmation before sending

**Always** proceed to Phase 3 (Pre-submission Review) of the orchestrator. Don't push to Submit.

## Hard rules

1. **Never invent metrics.** If a JD asks for "experience scaling X to Y users" and user only has Z, say Z — do not inflate.
2. **Master CV is canonical** — never modify it directly. Always work in a per-application copy.
3. **1 page always** for the CV (default; respect `config.yaml` if it overrides). If overflowing, drop the weakest bullet, not the strongest.
4. **Cover letter must cite something verifiable** about the company. If you cannot find anything specific, use WebFetch on the company's homepage / blog before writing — do not write generic praise.
5. **Visa line stays in About** if user's `memory/project.md` says they need it (e.g., "UK Graduate Visa eligible — no sponsorship required"). Drop it only if the JD explicitly invites either path.
6. **Tone:** terse, factual, builder voice. No "passionate", "thrive", "fast-paced environment", "leverage synergies". Cut these on sight.
7. **Read `memory/feedback.md` before writing.** It contains rules the user has already taught you. Apply them preemptively.

## Output structure (final state)

```
${WORKSPACE}/applications/<company>-<role>-<YYYY-MM-DD>/
├── analysis.md             # JD analysis
├── cv.tex / cv.pdf         # Tailored 1-page CV
├── cover_letter.md or .tex / .pdf  # 250-400 word cover letter
├── linkedin_dm.txt         # ≤60-word DM
└── why_i_fit.txt           # 3 bullets for application form
```

## Reference files

- Master CV: `${WORKSPACE}/cv_master.tex`
- Experience bank: `${WORKSPACE}/experience-bank/*.md`
- Templates: `${WORKSPACE}/templates/*.md`
- Build: `${WORKSPACE}/build.sh` (Tectonic-based, ATS-friendly LaTeX)
