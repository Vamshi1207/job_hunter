---
name: cv-tailor
description: Tactical sub-skill that takes one job description and produces a complete tailored application package — CV PDF (`config.yaml` cv_format.pages), 250-400 word cover letter, ≤60-word LinkedIn cold DM, 3-bullet "why I fit" summary. Triggered by user pasting a JD URL/text or saying "apply to this", or invoked by the job-hunt orchestrator skill in Phase 2. All outputs land under applications/<company>-<role>-<date>/. Reads writing rules from memory/feedback.md before generating.
---

# CV-Tailor (Generic)

You are tailoring application materials for one specific role. Always read the user's memory files first:

- `memory/project.md` — user profile + target market + eligibility (visa, location)
- `memory/feedback.md` — accumulated writing rules (what to do, what to avoid)

These encode hard rules that override your defaults.

## Workspace expectations

```
${WORKSPACE}/cv_master.md              ← canonical CV (never modified by this skill; length from config.yaml)
${WORKSPACE}/resumes/template.html     ← HTML render template
${WORKSPACE}/jobs.yaml                 ← add the JD, then run the pipeline
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

**Preferred:** append the JD to `jobs.yaml` and run `python3 -m pipeline.run_pipeline --job <company>`. That fills `resumes/template.html`, scores honesty, and writes PDF + playbook.

**If tailoring in-chat instead:**
1. Copy `resumes/template.html` → `applications/<company>-<role>-<date>/<Name>_CV.html`.
2. **Edit ONLY placeholder content** (do not restructure):
   - **Tagline**: adjust to match the role.
   - **About / summary**: rewrite using `experience-bank/about-variants.md` matching the role type. Tweak the final sentence to name the role / company.
   - **Job bullets**: for each employer, swap in bullets from `experience-bank/<employer>.md` that match the JD. When `cv_format.bullets.dynamic` is true, give more bullets to the best-matching employer (within `min`–`max`) and leave unused slots empty. When it is false, keep the configured `bullets` count. Fill leftover slots from `cv_master.md`. Never invent.
   - **Skills section**: reorder so technologies most relevant to the JD appear first. You may also add more skills, libraries, tools, and frameworks to the Key Skills section to support claims and boost ATS score, provided they are closely related to the candidate's actual work and stack (never completely out of the blue).
3. Ask the user to render with the pipeline, or leave HTML for them to export.
4. **Verify page count** against `config.yaml` `cv_format.pages`. If an extra page appears, drop the weakest bullet — do not shrink fonts.

### Step 3 — Cover letter

Use `templates/cover_letter.template.md` or write fresh. Fill placeholders:
- `{{COMPANY}}`, `{{ROLE}}`, `{{HIRING_MANAGER_OR_TEAM}}` (use "Hiring team" if unknown)
- `{{HOOK}}` — opens with the most JD-relevant accomplishment, NOT a generic intro
- `{{WHY_THIS_COMPANY}}` — 2–3 sentences citing something concrete you can verify (a launch, blog post, feature, recent funding). **Never write generic praise.**
- `{{WHY_ME}}` — 3 bullets max, each starting with a verb, each tied to one JD keyword
- `{{CLOSE}}` — short, no "looking forward to hearing from you" filler

Length target: **250–400 words**. Cut ruthlessly.

Save as `applications/<company>-<role>-<date>/cover_letter.md`.

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
- CV preview command (`open <pdf>`)
- One-paragraph summary of choices made: which bullets selected, which About variant, what cover letter angle
- Anything ambiguous in the JD that needs user confirmation before sending

**Always** proceed to Phase 3 (Pre-submission Review) of the orchestrator. Don't push to Submit.

## Hard rules

1. **Never invent metrics.** If a JD asks for "experience scaling X to Y users" and user only has Z, say Z — do not inflate.
2. **Master CV is canonical** — never modify it directly. Always work in a per-application copy.
3. **Page count is `config.yaml` `cv_format.pages`** (any positive integer). Do not hardcode 1 or 2. Do not shrink fonts to force a shorter CV. If content overflows, drop the weakest bullet.
4. **Cover letter must cite something verifiable** about the company. If you cannot find anything specific, use WebFetch on the company's homepage / blog before writing — do not write generic praise.
5. **Visa line stays in About** if user's `memory/project.md` says they need it (e.g., "UK Graduate Visa eligible — no sponsorship required"). Drop it only if the JD explicitly invites either path.
6. **Tone:** terse, factual, builder voice. No "passionate", "thrive", "fast-paced environment", "leverage synergies". Cut these on sight.
7. **Read `memory/feedback.md` before writing.** It contains rules the user has already taught you. Apply them preemptively.

## Output structure (final state)

```
${WORKSPACE}/applications/<company>-<role>-<YYYY-MM-DD>/
├── analysis.md             # JD analysis
├── <Name>_CV.html / .pdf   # Tailored CV
├── cover_letter.md         # 250-400 word cover letter
├── linkedin_dm.txt         # ≤60-word DM
├── why_i_fit.txt           # 3 bullets for application form
└── playbook.md             # paste-by-field; user clicks Submit
```

## Reference files

- Master CV: `${WORKSPACE}/cv_master.md`
- HTML template: `${WORKSPACE}/resumes/template.html`
- Experience bank: `${WORKSPACE}/experience-bank/*.md`
- Templates: `${WORKSPACE}/templates/*.md`
- Pipeline: `python3 -m pipeline.run_pipeline --job <company>`
