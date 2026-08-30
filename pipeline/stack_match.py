"""Keep hunt matches on the candidate's strong skills; LLM only when the stack is unclear."""

from __future__ import annotations

import json
import logging
import re

from pipeline.config import Config
from pipeline.search import phrase_in, preferred_skills, reject_skills

log = logging.getLogger(__name__)

# Primary programming languages. SQL is complementary and never a veto.
LANGUAGE_FAMILIES: dict[str, tuple[str, ...]] = {
    "python": ("python", "django", "flask", "fastapi", "pyspark", "pandas"),
    "javascript": ("javascript", "typescript", "node.js", "nodejs", "react"),
    "java": ("java", "spring boot", "kotlin"),
    "cpp": ("c++", "cpp"),
    "csharp": ("c#", ".net", "dotnet"),
    "go": ("golang",),
    "rust": ("rust",),
    "ruby": ("ruby", "rails"),
    "php": ("php",),
    "swift": ("swift", "objective-c"),
    "scala": ("scala",),
}

# Related languages the candidate can still pick up.
ADJACENT: dict[str, frozenset[str]] = {
    "python": frozenset({"javascript", "go", "scala"}),
    "javascript": frozenset({"python"}),
    "go": frozenset({"python"}),
    "scala": frozenset({"python"}),
}

BONUS_MARKERS = (
    "nice to have",
    "nice-to-have",
    "bonus",
    "preferred qualifications",
    "good to have",
    "plus:",
    "a plus",
    "is a plus",
    "assets:",
)
REQUIRED_CONTEXT = re.compile(
    r"(required|must have|must-have|proficient|expertise|years? of|experience (?:with|in)|minimum qualifications)",
    re.I,
)
GO_REQUIRED = re.compile(
    r"(golang|\bgo(?:lang)?\s+(?:developer|engineer)|proficient in go\b|experience (?:with|in) go\b)",
    re.I,
)


def split_required_bonus(jd: str) -> tuple[str, str]:
    text = jd or ""
    low = text.lower()
    cut = None
    for marker in BONUS_MARKERS:
        pos = low.find(marker)
        if pos == -1:
            continue
        sentence = max(low.rfind(".", 0, pos), low.rfind("\n", 0, pos))
        start = 0 if sentence == -1 else sentence + 1
        cut = start if cut is None else min(cut, start)
    if cut is None:
        return text, ""
    return text[:cut], text[cut:]


def user_language_families(cfg: Config) -> set[str]:
    found: set[str] = set()
    extra = cfg.get("hunt.strong_skills")
    skills = list(preferred_skills(cfg))
    if isinstance(extra, str) and extra.strip():
        skills.append(extra)
    elif extra:
        skills.extend(str(item) for item in extra)
    blob = " ".join(skills).lower()
    for family, aliases in LANGUAGE_FAMILIES.items():
        if any(phrase_in(blob, alias) for alias in aliases):
            found.add(family)
    return found or {"python"}


def allowed_families(cfg: Config) -> set[str]:
    mine = user_language_families(cfg)
    allowed = set(mine)
    for family in mine:
        allowed.update(ADJACENT.get(family) or ())
    for skill in reject_skills(cfg):
        low = (skill or "").strip().lower()
        for family, aliases in LANGUAGE_FAMILIES.items():
            if low in aliases or low == family:
                allowed.discard(family)
    return allowed


def _family_in_text(text: str, family: str) -> bool:
    if family == "go":
        return bool(GO_REQUIRED.search(text or ""))
    if family == "java":
        return phrase_in(text, "java") and not phrase_in(text, "javascript")
    return any(phrase_in(text, alias) for alias in LANGUAGE_FAMILIES[family])


def languages_in(text: str) -> set[str]:
    return {family for family in LANGUAGE_FAMILIES if _family_in_text(text, family)}


def required_languages(listing: dict) -> set[str]:
    title = listing.get("role") or ""
    jd = listing.get("jd") or ""
    required_text, _bonus = split_required_bonus(jd)
    found = languages_in(title)
    has_req_ctx = bool(REQUIRED_CONTEXT.search(required_text))
    for family in languages_in(required_text):
        if has_req_ctx or family in found:
            found.add(family)
    return found


def mentioned_languages(listing: dict) -> set[str]:
    return languages_in(f"{listing.get('role') or ''}\n{listing.get('jd') or ''}")


def stack_decision(listing: dict, cfg: Config) -> str:
    """Return keep, drop, or doubt for this posting vs the candidate's languages."""
    allowed = allowed_families(cfg)
    mine = user_language_families(cfg)
    title_langs = languages_in(listing.get("role") or "")
    required = required_languages(listing)
    mentioned = mentioned_languages(listing)
    required_text, bonus = split_required_bonus(listing.get("jd") or "")
    bonus_langs = languages_in(bonus)
    body_required = languages_in(required_text)

    foreign_title = title_langs - allowed
    if foreign_title and not (title_langs & mine):
        return "drop"

    if required and required & allowed:
        if required & mine:
            return "keep"
        if required - allowed:
            return "doubt"
        return "keep"

    if required and not (required & allowed):
        return "drop"

    if body_required and not (body_required & allowed) and (body_required - bonus_langs):
        if not (mentioned & mine):
            return "drop"

    if mentioned & mine:
        foreign_required = (body_required - allowed) - bonus_langs
        if foreign_required and not (body_required & mine):
            return "doubt"
        return "keep"

    foreign = mentioned - allowed
    if foreign and not (mentioned & allowed):
        if listing.get("jd"):
            return "doubt"
        return "drop"
    return "keep"


def apply_stack_gate(listing: dict, cfg: Config, *, ask_llm=None) -> bool:
    """True if hunt should tailor this posting. LLM is used only for doubt."""
    decision = stack_decision(listing, cfg)
    if decision == "keep":
        return True
    if decision == "drop":
        log.info(
            "Stack mismatch %s — %s (strong skills: %s)",
            listing.get("company"),
            listing.get("role"),
            ", ".join(sorted(user_language_families(cfg))),
        )
        return False
    if not listing.get("jd"):
        return False
    if cfg.get("hunt.stack_llm", True) is False:
        return False
    confirm = ask_llm or confirm_stack_with_llm
    try:
        ok = bool(confirm(listing, cfg))
    except Exception as exc:
        log.warning("Stack LLM check failed (%s); dropping %s", exc, listing.get("role"))
        return False
    if not ok:
        log.info("LLM dropped %s — %s (stack)", listing.get("company"), listing.get("role"))
    return ok


def confirm_stack_with_llm(listing: dict, cfg: Config) -> bool:
    from pipeline.llm import complete_prompt

    skills = ", ".join(preferred_skills(cfg)[:12]) or "Python"
    rejected = ", ".join(reject_skills(cfg)) or "none"
    jd = (listing.get("jd") or "")[:4000]
    prompt = f"""Decide if this job matches the candidate's strong skills.

Candidate strong skills: {skills}
Reject skills: {rejected}
The candidate is a software/data engineer. They can pick up a related language (for example TypeScript or Go next to Python). They should not be matched to a role whose day-to-day work is a different core language such as C++ or Java.

Job title: {listing.get("role") or ""}
Company: {listing.get("company") or ""}
Location: {listing.get("location") or ""}

Job description:
{jd}

Return JSON only:
{{"match": true or false, "reason": "one short sentence"}}
match=true if the candidate's stack can do the core work. match=false if the role is primarily another language."""
    raw = complete_prompt(prompt, effort="low")
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return False
    data = json.loads(match.group(0))
    return bool(data.get("match"))
