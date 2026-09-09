"""Fabrication freedom (0–5): how far the tailor LLM may go beyond the master CV."""

from __future__ import annotations

import re
from pathlib import Path

from pipeline.config import Config, load_config

LEVEL_MIN = 0
LEVEL_MAX = 5
DEFAULT_LEVEL = 1

LEVEL_LABELS = {
    0: "Strict — master CV / memory / bank only",
    1: "Keyword alignment — rephrase existing work only",
    2: "Related skills — Key Skills extensions only",
    3: "Adjacent reframing — broaden real bullets carefully",
    4: "Measured stretch — limited plausible adjacent skills",
    5: "Strong ATS push — limited fabrication only if needed",
}

# Absolute honesty floors calibrated at ats_threshold=80.
# If threshold changes, floors shift by the same delta (L2 stays == threshold).
# Kept high on purpose — no extreme fabrication even at level 5.
_HONESTY_FLOOR_AT_80 = (90, 85, 80, 75, 72, 70)


def clamp_level(value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        level = DEFAULT_LEVEL
    return max(LEVEL_MIN, min(LEVEL_MAX, level))


def fabrication_freedom(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    return clamp_level(cfg.get("pipeline.fabrication_freedom", DEFAULT_LEVEL))


def level_label(level: int) -> str:
    level = clamp_level(level)
    return LEVEL_LABELS[level]


def honesty_gate(freedom: int, ats_threshold: int) -> int:
    """Minimum honesty required to accept a tailor attempt. Score still must hit ats_threshold."""
    freedom = clamp_level(freedom)
    try:
        threshold = int(ats_threshold)
    except (TypeError, ValueError):
        threshold = 80
    return max(0, min(100, _HONESTY_FLOOR_AT_80[freedom] + (threshold - 80)))


def freedom_instructions(level: int, *, pages: int, ats_threshold: int) -> str:
    """Prompt block injected into the tailor writer."""
    level = clamp_level(level)
    page_word = "page" if pages == 1 else "pages"
    header = (
        f"### FABRICATION FREEDOM — level {level}/5 ({LEVEL_LABELS[level]})\n"
        f"Always chase ATS score ≥ {ats_threshold} AND fit the CV into exactly "
        f"{pages} {page_word} (prefer fewer bullets / shorter lines over overflow). "
        f"Never invent employers, education institutions, or employment dates."
    )
    bodies = {
        0: (
            "STRICT MODE. Use ONLY facts present in the master CV, memory, or experience bank.\n"
            "- You may reorder, shorten, or rephrase existing bullets and skills.\n"
            "- You MUST NOT add skills, tools, libraries, metrics, responsibilities, projects, "
            "or domains that are absent from those sources.\n"
            "- Prefer an honest gap over padding. Do not exaggerate scope or impact."
        ),
        1: (
            "KEYWORD ALIGNMENT. Stay inside the source of truth.\n"
            "- You may inject JD wording onto work the candidate already did.\n"
            "- You MUST NOT add new skills, tools, metrics, or responsibilities.\n"
            "- Do not inflate titles, scale, or impact beyond the source text."
        ),
        2: (
            "RELATED SKILLS. Experience bullets stay grounded in the source of truth.\n"
            "- Key Skills only: you MAY add closely related libraries/tools that are natural "
            "extensions of the verified stack (e.g. Pydantic next to FastAPI).\n"
            "- You MUST NOT invent metrics, employers, job titles, projects, or out-of-the-blue domains.\n"
            "- Do not invent technologies in experience bullets that the candidate did not use."
        ),
        3: (
            "ADJACENT REFRAMING. Prefer truth, then slight broadening.\n"
            "- You MAY reframe real work with adjacent-evidence language when the bank supports it.\n"
            "- Key Skills: related extensions allowed (same as level 2).\n"
            "- You MUST NOT invent fake metrics, employers, seniority titles, or projects.\n"
            "- Soften rather than fabricate when a JD keyword is missing."
        ),
        4: (
            "MEASURED STRETCH. Prefer truth; small stretches only when a JD keyword is otherwise missing.\n"
            "- You MAY add a few closely adjacent skills or soft quantification that you could defend in an interview.\n"
            "- You MAY slightly broaden responsibilities that are directionally consistent with real roles.\n"
            "- Do NOT invent whole projects, fake employers, fake education, Staff/Principal titles, "
            "or large/impressive metrics. Keep fabrication minimal."
        ),
        5: (
            "STRONG ATS PUSH. Still stay interview-defensible — no extreme fabrication.\n"
            "- You MAY add limited missing skills/tools or modest metrics only when needed to reach the ATS threshold.\n"
            "- Prefer rephrasing and related Key Skills over invention. Never invent employers, degrees, "
            "seniority titles, or large fake scale claims.\n"
            "- Still fit {pages} {page_word} — cut lower-value bullets rather than overflowing."
        ),
    }
    body = bodies[level]
    if level == 5:
        body = body.format(pages=pages, page_word=page_word)
    return f"{header}\n{body}"


def critic_freedom_rules(level: int) -> str:
    """How the ATS critic should treat honesty at this freedom level."""
    level = clamp_level(level)
    rules = {
        0: (
            "- Freedom 0: honesty 100 only if every claim is in the source of truth. "
            "Deduct for any added skill, tool, metric, or responsibility.\n"
            "- NEVER tell the writer to invent or fabricate.\n"
            "- allowed_fixes: reorder, rephrase existing content, inject JD wording for skills already present, tighten summary, drop overflow bullets."
        ),
        1: (
            "- Freedom 1: honesty high if claims map to source of truth. "
            "Deduct for new skills/tools/metrics not in the source.\n"
            "- NEVER tell the writer to invent new experience.\n"
            "- allowed_fixes: reorder, JD-keyword injection on existing work, tighten summary, drop overflow bullets."
        ),
        2: (
            "- Freedom 2: closely related Key Skills additions that extend the verified stack "
            "MUST NOT be penalized as dishonesty. Deduct for fabricated metrics, fake employers, "
            "false titles, or out-of-the-blue technologies.\n"
            "- Do not tell the writer to invent metrics or employers.\n"
            "- allowed_fixes: add related Key Skills, reorder, swap bank variants, inject JD wording for known skills, tighten summary, drop overflow bullets."
        ),
        3: (
            "- Freedom 3: adjacent reframing of real bullets and related Key Skills are allowed. "
            "Deduct for invented metrics, fake employers, false seniority, or fabricated projects.\n"
            "- allowed_fixes: adjacent reframing, related Key Skills, reorder, bank variants, tighten summary, drop overflow bullets."
        ),
        4: (
            "- Freedom 4: small adjacent skill stretches and modest soft metrics are allowed; "
            "still deduct for fake employers, fake education, Staff/Principal, invented projects, "
            "or large/impressive metrics.\n"
            "- Critique may suggest limited JD keyword coverage within level-4 bounds — not wholesale invention.\n"
            "- allowed_fixes: limited adjacent skills/soft metrics, slight embellishment of real responsibilities, reorder, drop overflow bullets."
        ),
        5: (
            "- Freedom 5: honesty must stay high (gate stays elevated). Limited skill/tool/metric additions "
            "are OK only if interview-defensible; fake employers/education/seniority/large scale claims still forbidden.\n"
            "- Critique should close ATS gaps with minimal fabrication and fit the page budget.\n"
            "- allowed_fixes: limited skills/metrics for ATS, reorder, drop overflow bullets — never extreme invent."
        ),
    }
    return rules[level]


def retry_freedom_rules(level: int) -> str:
    level = clamp_level(level)
    if level <= 1:
        return (
            "You may ONLY: reorder skills, inject JD wording for skills already in the master CV or bank, "
            "swap experience-bank variants, tighten the summary, or drop bullets to fit the page budget. "
            "You MUST NOT invent technologies, domains, employers, job titles, metrics, or responsibilities."
        )
    if level == 2:
        return (
            "You may: add related skills to Key Skills, reorder skills, swap bank variants, "
            "inject JD wording for known skills, tighten the summary, or drop bullets for page fit. "
            "You MUST NOT invent metrics, employers, job titles, or out-of-the-blue technologies."
        )
    if level == 3:
        return (
            "You may: adjacent-reframe real bullets, add related Key Skills, reorder, swap bank variants, "
            "tighten summary, or drop bullets for page fit. "
            "You MUST NOT invent fake metrics, employers, seniority titles, or projects."
        )
    if level == 4:
        return (
            "You may add limited adjacent skills and modest soft metrics, slightly broaden "
            "directionally-real responsibilities, reorder, and drop bullets for page fit. "
            "Do not invent employers, education, Staff/Principal, projects, or large fake metrics."
        )
    return (
        "You may add limited skills, tools, or modest metrics only if needed to hit the ATS threshold. "
        "Prefer rephrase and related Key Skills over invention. Keep real employers and dates. "
        "No extreme fabrication, fake seniority, or large scale claims. Fit the page count by dropping lower-value bullets."
    )


def update_fabrication_freedom(cfg_path: Path, level: int) -> int:
    """Persist fabrication_freedom into config.yaml (preserves most comments)."""
    level = clamp_level(level)
    path = Path(cfg_path)
    text = path.read_text() if path.exists() else ""
    if re.search(r"(?m)^\s*fabrication_freedom\s*:", text):
        text = re.sub(
            r"(?m)^(?P<indent>\s*)fabrication_freedom\s*:.*$",
            rf"\g<indent>fabrication_freedom: {level}",
            text,
            count=1,
        )
    elif re.search(r"(?m)^pipeline:\s*$", text):
        text = re.sub(
            r"(?m)^(pipeline:\s*\n)",
            rf"\1  fabrication_freedom: {level}\n",
            text,
            count=1,
        )
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"\npipeline:\n  fabrication_freedom: {level}\n"
    path.write_text(text)
    load_config(force=True)
    return level


def settings_payload(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    level = fabrication_freedom(cfg)
    threshold = int(cfg.get("pipeline.ats_threshold", 80) or 80)
    return {
        "fabrication_freedom": level,
        "label": level_label(level),
        "levels": [
            {"value": i, "label": LEVEL_LABELS[i]} for i in range(LEVEL_MIN, LEVEL_MAX + 1)
        ],
        "ats_threshold": threshold,
        "honesty_gate": honesty_gate(level, threshold),
        "pages": cfg.cv_pages,
    }
