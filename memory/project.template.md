---
name: <Your name> Job Hunt
description: Profile snapshot, target market, eligibility — used by the pipeline tailor prompt and by job-hunt skill phases
type: project
---

# Profile snapshot — copy to memory/project.md (gitignored)

```bash
cp memory/project.template.md memory/project.md
```

Fill this in honestly. Date-stamp it. Update at least every 3 months. Contact, visa, and target roles also live in `config.yaml` (`user`, `visa`, `career`); keep them consistent.

Status as of YYYY-MM-DD:

## Verified background

- **Anchor project**: name, link, what it does, real numbers (e.g. "App on UK App Store, 800 paying subs at £3.99/mo as of May 2026")
- **Distribution channel**: type and verified follower / subscriber count, primary acquisition path for anchor project
- **Public GitHub / Portfolio**: URL + 1-line each on top 3 repos (with star counts)
- **LinkedIn URL** (custom-handle preferred)
- **Education**: degree, institution, dates, grade if good (top quartile of local equivalent)
- **Honours / awards**: actual ones, with the granting body
- **Visa**: status verbatim (must match `config.yaml` `visa.description`)
- **Prior work / internships**: dates, role, what was actually done — be honest, don't inflate

## Why this profile is interesting (1 paragraph)

What's the unusual thing about you? This frames default positioning for cover letters and the CV summary.

## How any LLM should treat this profile

When working on this user's CV, cover letters, application strategy, or job-search automation, treat the [anchor project + GitHub + distribution channel] stack as the core asset. Default positioning: "<role-archetype>" — e.g., "Senior IC, Python data/platform" — not a title you have not held. Target roles: keep in sync with `config.yaml` `career.target_roles`. Master CV is `cv_master.md`; PDFs are built by the Docker desk / `pipeline.run_pipeline`.

---

## Customisation notes (for the LLM at runtime)

- Update this file when something material changes (new product launch, new repo, finished degree, visa transition).
- The pipeline loads `memory/project.md` on every tailor. Empty is allowed; fill it for better letters.
- If you have multiple "anchor" assets, list both — the skill picks based on JD signal.
