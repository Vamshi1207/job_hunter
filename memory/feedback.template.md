---
name: CV writing preferences
description: Accumulated rules for how to write THIS user's CVs / cover letters — loaded by the pipeline tailor and by Phase 7 of the job-hunt skill
type: feedback
---

# CV writing rules

Copy to `memory/feedback.md` (gitignored):

```bash
cp memory/feedback.template.md memory/feedback.md
```

When writing or editing materials for this user:

## Generic rules (ship with the template)

1. **Default positioning** is `<role-archetype>` — edit that phrase in `memory/project.md` and keep titles honest (do not claim Staff/Principal unless that is the real title).

2. **Concrete tech stacks beat AI-tool name-dropping.** Project lines must show real frameworks (e.g. FastAPI, Kafka, PySpark), not "Cursor + Claude". AI coding tools belong in Skills if at all.

3. **No pitch-deck phrasing.** Cut "10x", "redefining X", "passionate about". Use concrete capability + behaviour.

4. **Never invent metrics.** If the user has not confirmed a number, drop the claim.

4a. **Don't claim measurement frameworks the user hasn't actually built.** "Cohort analysis", "retention model", "funnel analysis" only if confirmed.

5. **Lead with capability, demote specifics.** Bullet 1 = what the system does; bullet 2 = evidence.

6. **Section order** follows `config.yaml` `cv_format.section_order` (desk/PDF). Chat skills should not invent a different order.

7. **Page count follows `config.yaml` `cv_format.pages`.** Do not shrink type to force a shorter CV. ATS-friendly: no tables in main content, plain section headings, no images.

8. **Why these rules:** keep one legible persona recruiters can place in 10 seconds.

9. **How to apply:** Apply rules 1–7 by default. Only ask permission when a content claim might exceed what the user has verified.

---

## User-specific rules (added over time)

(This section starts empty. After you critique a draft, add a numbered rule here. Do not delete — they encode preferences that took real applications to discover.)

---

## Maintenance

- Audit this file every 5 applications. Update stale rules instead of leaving them.
- Don't bloat. Every tailor loads this file.
