# Experience Bank

This directory holds **multiple bullet variants per project** — the cv-tailor skill picks from these when generating role-specific CVs.

## Why multiple variants?

A single project (say, your iOS app) can be framed as:

- **Engineering evidence**: "Built X using Y framework, integrated Z API, deployed via W."
- **Growth evidence**: "Acquired N users via channel M, owned end-to-end funnel."
- **Operations evidence**: "Ran a one-person business including W, X, Y, Z."
- **Product evidence**: "Defined and shipped v1 of P based on Q user research."

A senior engineer reading your CV cares about the engineering frame. A growth lead cares about the acquisition frame. **Same project, different bullets.** This bank is the place where you write all of them in advance, so the skill can pick the right ones at runtime.

## File structure

One `.md` file per project. See `example-project.md` for the recommended layout.

```
experience-bank/
├── README.md                     ← this file
├── example-project.md            ← reference template (replace)
├── about-variants.md             ← short About paragraph variants for the CV header
├── <your-project-1>.md
├── <your-project-2>.md
└── ...
```

## Naming convention

- Use lowercase + dashes for filenames: `my-app.md`, `mlops-tracker.md`, etc.
- Keep `about-variants.md` for short About-paragraph variants — those go in the CV's About section, not the Projects section.

## How variants are selected at runtime

When you paste a JD URL, cv-tailor:
1. Reads the JD, classifies the role type (Growth, Engineering, Data, BizOps, etc.)
2. For each project, picks the variant whose framing matches the role type.
3. Keep the template bullet counts. Respect `config.yaml` `cv_format.pages` — do not trim just to squeeze onto fewer pages.

If your project has fewer variants than the skill needs, it falls back to the closest one. Add more variants when you notice repeated mismatches.

## Things NOT to write here

- ❌ Specific metrics you cannot back up. Don't write "100K MAU" if you actually have ~10K.
- ❌ Future / aspirational claims. "Plan to launch Q3" doesn't belong on a CV.
- ❌ Confidential employer info — internal numbers, unreleased product names.

If you're unsure whether a number is OK to claim, leave it out. The skill respects this rule via `memory/feedback.md` defaults.

## Tip: write project bank from your master CV first

The fastest way to seed this directory:
1. Have your master CV done.
2. For each project, copy the bullets into `<project>.md` as variant V1.
3. Then add 2–4 more variants by reframing the same project for different role types.

The skill's value compounds with bank depth. After 3 applications, you'll learn which framings get callbacks and which don't — keep iterating.
