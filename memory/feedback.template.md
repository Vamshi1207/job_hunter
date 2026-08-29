---
name: CV writing preferences
description: Accumulated rules for how to write THIS user's CVs / cover letters — populated by Phase 7 of the job-hunt skill over time
type: feedback
---

# CV writing rules

When writing or editing materials for this user:

## Generic rules (ship with the template)

1. **Default positioning** is `<role-archetype>` — not generic SWE, not pure economist. Lead with builder/product/AI; demote academic specifics. (Edit `<role-archetype>` in your `memory/project.md`.)

2. **Concrete tech stacks beat AI-tool name-dropping.** Project lines must show real frameworks (e.g., SwiftUI, FastAPI, PyTorch, Postgres), not "Cursor + Claude Code + Codex". AI coding tools belong in the Skills section, not in the project header.

3. **No pitch-deck phrasing.** "Anti-spoonfeeding philosophy", "10x productivity", "redefining X" all get cut. Use concrete capability + behaviour ("Designed scaffolded learning flows around guided steps rather than answer dumping").

4. **Never invent metrics.** Don't write "reducing churn", "X% retention lift" etc. unless the user has confirmed the number. If unsure, drop the claim.

4a. **Don't claim measurement frameworks the user hasn't actually built.** Phrases like "weekly cohort review", "cohort analysis", "retention model", "funnel analysis" imply structured analytical work. Use only if user has confirmed they built actual cohort tables / retention models. Otherwise use softer language: "weekly user-feedback and performance reviews", "iterated based on subscriber feedback". Reason: face-to-face interview will probe — overclaiming triggers a ding.

5. **Lead with capability, demote specifics.** For research / pipeline projects: bullet 1 = what the system does at the capability level; bullet 2 = the validation evidence. Not the reverse.

6. **Section order for AI-startup CVs** (junior): About → Projects → Skills → Education → Experience. Skills before Education because portfolio matters more than credentials at AI startups. (Other markets: see `docs/customization.md` for senior IC / academic / consulting variants.)

7. **Page count follows `config.yaml` `cv_format.pages`.** Set that integer to whatever this user needs (1, 2, 3, …). Do not shrink type to force a shorter CV. ATS-friendly: no tables in main content, plain section headings, no images.

8. **Why these rules:** the user has been observed to be competent at self-evaluation but to occasionally drift toward "founder bio" or "campaign deck" tone in drafts. Compress everything into a single legible persona that recruiters can place in 10 seconds.

9. **How to apply:** Apply rules 1–7 by default. Only ask permission when a content claim might exceed what user has verified (e.g., specific numbers, retention rates).

---

## User-specific rules (added by Phase 7 over time)

(This section starts empty. As you give review feedback during Phase 3, the skill writes new rules here. Do not delete — they encode preferences that took real applications to discover.)

(Example placeholder for what Phase 7 adds:)

> 10. **Don't list `<specific-skill-X>` as a top-line skill** unless the JD explicitly mentions it. Reason: came up after <company> recruiter call; user said they felt over-claimed when probed in interview.

> 11. **For <industry-vertical> roles**, lead the About paragraph with `<specific-framing>` rather than the default `<role-archetype>`. Reason: user noticed higher callback rate in <date-range> when this framing was used.

---

## Maintenance

- Audit this file every 5 applications. If a rule is no longer relevant (e.g. you DID build a cohort table since rule 4a was added), update it explicitly rather than leaving stale guidance.
- Don't bloat. If you have 25 rules, the skill loads them all every Phase entry — that costs tokens. Consolidate.
