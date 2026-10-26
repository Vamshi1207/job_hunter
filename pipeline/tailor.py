"""Tailor an HTML CV from the master CV + experience bank. Freedom level controls invention."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path

from pipeline.bank import load_experience_bank, load_optional
from pipeline.config import Config, load_config, should_generate_cover_letter
from pipeline.cv_format import (
    apply_cv_format,
    bullet_count_max,
    bullet_count_min,
    bullet_counts_dynamic,
    page_height_px,
    page_size,
    tailor_layout_instructions,
)
from pipeline.fabrication import (
    critic_freedom_rules,
    fabrication_freedom,
    freedom_instructions,
    retry_freedom_rules,
)
from pipeline.llm import complete_prompt

log = logging.getLogger(__name__)

# Fallback taglines when `tailor.approved_titles` is not configured.
# Real per-user lists live in config.yaml so no personal titles sit in code.
DEFAULT_APPROVED_TITLES = [
    "Software Engineer — Backend Systems",
    "Software Engineer — Data & Distributed Systems",
    "Software Engineer — APIs & Platform Engineering",
    "Backend Engineer — Services & Data Pipelines",
]


def approved_titles(cfg: Config | None = None) -> list[str]:
    """Taglines the tailor prompt may pick from. Per-user config, not code."""
    cfg = cfg or load_config()
    raw = cfg.get("tailor.approved_titles")
    if isinstance(raw, str):
        raw = [raw]
    titles = [str(t).strip() for t in (raw or []) if str(t).strip()]
    return titles or list(DEFAULT_APPROVED_TITLES)


DEFAULT_JOB_BLOCKS = [
    {
        "prefix": "JOB1",
        "employer": "Example One",
        "default_title": "Software Engineer",
        "bullets": 7,
    },
    {
        "prefix": "JOB2",
        "employer": "Example Two",
        "default_title": "Software Engineer",
        "bullets": 4,
    },
    {
        "prefix": "JOB3",
        "employer": "Example Three",
        "default_title": "Software Engineer",
        "bullets": 6,
    },
]


def _positive_int(value, default: int) -> int:
    try:
        val = int(value)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


# Re-exported from pipeline.config for backwards compatibility
__all_cover_letter = should_generate_cover_letter


def job_blocks(cfg: Config | None = None) -> list[dict]:
    """Employer blocks come from config.yaml `experience.jobs` so personal employers stay local."""
    cfg = cfg or load_config()
    dynamic = bullet_counts_dynamic(cfg)
    floor = bullet_count_min(cfg)
    ceiling = bullet_count_max(cfg)
    raw = cfg.get("experience.jobs")
    if isinstance(raw, list):
        out = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("prefix"):
                continue
            configured = None
            if item.get("bullets") not in (None, ""):
                configured = _positive_int(item.get("bullets"), 4)
            bullets_min = _positive_int(item.get("bullets_min"), floor)
            if item.get("bullets_max") not in (None, ""):
                bullets_max = _positive_int(item.get("bullets_max"), ceiling)
            elif dynamic:
                bullets_max = max(ceiling, configured or ceiling)
            else:
                bullets_max = configured or 4
            if not dynamic:
                bullets_min = bullets_max
            bullets_min = min(bullets_min, bullets_max)
            out.append(
                {
                    "prefix": str(item["prefix"]).upper().replace(" ", ""),
                    "employer": str(item.get("employer") or item["prefix"]),
                    "default_title": str(item.get("default_title") or "Software Engineer"),
                    "bullets": bullets_max,
                    "bullets_min": bullets_min,
                    "bullets_max": bullets_max,
                    "dynamic": dynamic,
                }
            )
        if out:
            return out
    out = []
    for block in DEFAULT_JOB_BLOCKS:
        configured = _positive_int(block.get("bullets"), 4)
        bullets_max = max(ceiling, configured) if dynamic else configured
        bullets_min = min(floor, bullets_max) if dynamic else bullets_max
        out.append(
            {
                **block,
                "bullets": bullets_max,
                "bullets_min": bullets_min,
                "bullets_max": bullets_max,
                "dynamic": dynamic,
            }
        )
    return out


def _soft_bullet_total(cfg: Config, jobs: list[dict]) -> tuple[int, int]:
    pages = cfg.cv_pages
    floor = sum(int(job["bullets_min"]) for job in jobs)
    ceiling = sum(int(job["bullets_max"]) for job in jobs)
    target_lo = min(ceiling, max(floor, pages * 7))
    target_hi = min(ceiling, max(target_lo, pages * 9))
    return target_lo, target_hi


def _bullet_count_instruction(cfg: Config) -> str:
    jobs = job_blocks(cfg)
    if not any(job.get("dynamic") for job in jobs):
        counts = ", ".join(f"{job['employer']} {job['bullets']}" for job in jobs)
        return f"Keep the exact bullet counts: {counts}."
    ranges = ", ".join(
        f"{job['employer']} {job['bullets_min']}–{job['bullets_max']}" for job in jobs
    )
    lo, hi = _soft_bullet_total(cfg, jobs)
    pages = cfg.cv_pages
    page_word = "page" if pages == 1 else "pages"
    return (
        "Bullet counts are dynamic for this JD. For each employer fill consecutive B1…Bk "
        "(k within the range below) and leave later Bn tags empty. "
        "Give more bullets to the employer whose work best matches the JD; keep weaker matches closer to the minimum. "
        "Do not invent bullets to hit the maximum. "
        f"Across all jobs aim for about {lo}–{hi} bullets total so the CV stays {pages} {page_word}. "
        f"Per employer: {ranges}."
    )


_BULLET_LI_RE = re.compile(
    r'(?P<full>(?P<indent>[ \t]*)<li>\s*(?:<span class="point">)?\{\{(?P<prefix>[A-Z][A-Z0-9]*)_B(?P<n>\d+)\}\}(?:</span>)?\s*</li>)',
    re.IGNORECASE,
)
_UNUSED_BULLET_LI_RE = re.compile(
    r'\n?[ \t]*<li>\s*(?:<span class="point">)?\{\{[A-Z][A-Z0-9]*_B\d+\}\}(?:</span>)?\s*</li>',
    re.IGNORECASE,
)
_JOB_BULLET_TAG_RE = re.compile(r"^[A-Z][A-Z0-9]*_B\d+$")


def ensure_bullet_slots(html: str, jobs: list[dict] | None = None) -> str:
    """Pad each job <ul> so placeholders exist up to that job's max bullet count."""
    for job in jobs or job_blocks():
        prefix = job["prefix"]
        needed = int(job["bullets"])
        matches = [m for m in _BULLET_LI_RE.finditer(html) if m.group("prefix").upper() == prefix]
        if not matches:
            continue
        highest = max(int(m.group("n")) for m in matches)
        if highest >= needed:
            continue
        last = matches[-1]
        extras = [
            f'{last.group("indent")}<li><span class="point">{{{{{prefix}_B{i}}}}}</span></li>'
            for i in range(highest + 1, needed + 1)
        ]
        html = html[: last.end()] + "\n" + "\n".join(extras) + html[last.end() :]
    return html


def strip_unused_bullet_placeholders(html: str) -> str:
    """Drop leftover {{PREFIX_Bn}} list items after a dynamic tailor run."""
    return _UNUSED_BULLET_LI_RE.sub("", html)


def _is_job_bullet_tag(tag: str) -> bool:
    return bool(_JOB_BULLET_TAG_RE.match(tag))


SKILL_BLOCKS = [
    ("SKILL_LANG", "Programming languages"),
    ("SKILL_ML", "AI & Agentic Systems"),
    ("SKILL_DATA", "Distributed systems & data"),
    ("SKILL_BACKEND", "Backend & APIs"),
    ("SKILL_CLOUD", "Cloud-Native & DevOps"),
]

EXTRA_TAGS = ["COVER_LETTER", "LINKEDIN_DM", "WHY_I_FIT", "ROLE_TYPE", "ANALYSIS"]


def resume_tags(cfg: Config | None = None) -> list[str]:
    tags = ["TITLE", "SUMMARY"]
    for job in job_blocks(cfg):
        tags.append(f"{job['prefix']}_TITLE")
        tags.extend(f"{job['prefix']}_B{i}" for i in range(1, job["bullets"] + 1))
    tags.extend(name for name, _ in SKILL_BLOCKS)
    return tags


def all_tags(cfg: Config | None = None) -> list[str]:
    cfg = cfg or load_config()
    extras = [t for t in EXTRA_TAGS if t != "COVER_LETTER" or should_generate_cover_letter(cfg)]
    return resume_tags(cfg) + extras


def _tag_schema(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    gen_cover = should_generate_cover_letter(cfg)
    lines = [
        "<R_TITLE>why this tagline</R_TITLE>",
        "<TITLE>one-line tagline. YOU MUST PICK EXACTLY ONE OF THE PRE-APPROVED TITLES LISTED IN THE INSTRUCTIONS. DO NOT invent your own title.</TITLE>",
        "<R_SUMMARY>why this summary / which about-variant</R_SUMMARY>",
        "<SUMMARY>2-3 sentence prose paragraph grounded in the master CV and written in its voice. No semicolon-separated keyword lists, no em dashes, and do not start with '[Role]-shaped' or 'Forward Deployed-shaped'.</SUMMARY>",
        "",
    ]
    for job in job_blocks(cfg):
        prefix = job["prefix"]
        lines.append(f"<R_{prefix}_TITLE>why this title wording</R_{prefix}_TITLE>")
        lines.append(f"<{prefix}_TITLE>{job['default_title']}</{prefix}_TITLE>")
        for i in range(1, job["bullets"] + 1):
            lines.append(f"<R_{prefix}_B{i}>why this bullet / which bank variant</R_{prefix}_B{i}>")
            lines.append(f"<{prefix}_B{i}>bullet text, or empty if unused</{prefix}_B{i}>")
        lines.append("")
    for name, label in SKILL_BLOCKS:
        lines.append(f"<R_{name}>why this order</R_{name}>")
        lines.append(f"<{name}>comma-separated list only — do not repeat '{label}:'</{name}>")
    lines.append("")
    if gen_cover:
        lines.extend([
            "<R_COVER_LETTER>cover letter angle</R_COVER_LETTER>",
            "<COVER_LETTER>250-400 word cover letter</COVER_LETTER>",
        ])
    lines.extend(
        [
            "<R_LINKEDIN_DM>why this hook</R_LINKEDIN_DM>",
            "<LINKEDIN_DM>≤60 word cold DM</LINKEDIN_DM>",
            "<R_WHY_I_FIT>which JD requirements these map to</R_WHY_I_FIT>",
            "<WHY_I_FIT>exactly 3 bullets, each ≤25 words</WHY_I_FIT>",
            "<ROLE_TYPE>primary role type (e.g. Engineering, Data, FDE, Platform)</ROLE_TYPE>",
            "<ANALYSIS>short markdown: keywords, strongest evidence, honest gaps, bullets selected from bank</ANALYSIS>",
        ]
    )
    return "\n".join(lines)


def _retry_section(feedback_history: str, cfg: Config | None = None) -> str:
    if not feedback_history:
        return ""
    cfg = cfg or load_config()
    level = fabrication_freedom(cfg)
    return f"""
### PREVIOUS ATTEMPT FEEDBACK
{feedback_history}

{retry_freedom_rules(level)}
RETRY DIRECTIVE:
- If previous attempt failed honesty: remove or soften the flagged unearned claims, restoring verified facts from the master CV / bank.
- If previous attempt failed ATS score / keyword coverage: add the missing hard keywords to Key Skills only where the master CV or bank supports them. Weave at most one or two into the most relevant bullets. Do not stuff, repeat, or force keywords.
- If previous attempt failed the voice or 10-second scan: fix only the flagged sections. Vary bullet openings and lengths, remove metric stuffing and keyword salad, replace AI vocabulary, and restore the master CV's rhythm.
- If a JD requirement cannot be met within the current fabrication freedom level, leave it as a gap or drop a lower-value bullet to protect the page budget.
"""


VOICE_CONTRACT = """
### VOICE CONTRACT (second only to truthfulness)
The master CV is the voice reference. Match its rhythm, diction, and level of detail. The result must read like the same person wrote it for this role, not like an ATS keyword report.
- Summary: a 2-3 sentence prose paragraph. No semicolon-separated keyword lists, no em dashes, no "At X: did Y" inventory.
- Bullets: vary openings and lengths. No more than two bullets per employer may start with the same verb. Do not force a metric into every bullet; use only numbers that appear in the master CV or bank, at most one numeric metric per bullet. At least one bullet per employer should explain a technical decision or tradeoff, not just an outcome.
- Use the XYZ shape (accomplished X, measured by Y, by doing Z) for at most two bullets per employer.
- Put most JD keywords in Key Skills. Use a JD term in a bullet only when the source supports it and it reads naturally. Never repeat a keyword to raise density.
- Avoid: leveraged, robust, seamless, comprehensive, holistic, spearheaded, single-handedly, delve, drive meaningful impact, at its core, it is important to note, em dashes.
- Do not use the same sentence frame more than twice across the whole resume.
""".strip()

VOICE_CRITICAL_FLAGS = {
    "uniform_openers",
    "metric_stuffing",
    "keyword_stuffing",
    "ai_vocabulary",
    "em_dash_overuse",
    "repeated_frames",
    "unsupported_claim",
    "title_inflation",
    "generic_summary",
}


def _setting_enabled(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def jd_brief_enabled(cfg: Config | None = None) -> bool:
    cfg = cfg or load_config()
    return _setting_enabled(cfg.get("pipeline.jd_brief"), True)


def voice_gate_enabled(cfg: Config | None = None) -> bool:
    cfg = cfg or load_config()
    return _setting_enabled(cfg.get("pipeline.voice_gate"), True)


def voice_rewrite_enabled(cfg: Config | None = None) -> bool:
    cfg = cfg or load_config()
    return _setting_enabled(cfg.get("pipeline.voice_rewrite"), True)


def voice_threshold(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    try:
        return max(0, min(100, int(cfg.get("pipeline.voice_threshold", 75))))
    except (TypeError, ValueError):
        return 75


def voice_sameness_max(cfg: Config | None = None) -> int:
    cfg = cfg or load_config()
    try:
        return max(0, min(100, int(cfg.get("pipeline.voice_sameness_max", 40))))
    except (TypeError, ValueError):
        return 40


def _clean_string_list(value, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _extract_json_object(text: str) -> dict:
    text = text or ""
    start = text.find("{")
    while start != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError):
            pass
        start = text.find("{", start + 1)
    return {}


def format_jd_brief(brief: dict | None) -> str:
    if not brief:
        return "(no brief available; prioritize the JD's must-haves only where the master CV and bank support them)"
    def join(key: str) -> str:
        values = brief.get(key) or []
        return ", ".join(str(v) for v in values) if values else "none"

    return (
        f"- Match score: {brief.get('match_score', 'n/a')}/100\n"
        f"- Must-have keywords: {join('must_have_keywords')}\n"
        f"- Nice-to-have keywords: {join('nice_to_have_keywords')}\n"
        f"- Honest missing keywords: {join('missing_keywords')}\n"
        f"- Recruiter red flags: {join('red_flags')}\n"
        f"- 10-second skip risks: {join('skip_risks')}\n"
        f"- Strongest truthful angle: {brief.get('angle') or 'none'}"
    )


def build_jd_brief_prompt(cfg: Config, company: str, role: str, jd_text: str) -> str:
    master = load_optional(cfg.master_cv_path)
    bank = load_experience_bank(cfg.experience_bank_dir)
    return f"""
You are an ATS parser and a senior recruiter with 15 years of hiring experience. Read the job description and the candidate's source of truth, then produce a short brief for the writer.

### Job description
{jd_text}

### Master CV (source of truth)
{master}

### Experience bank (evidence)
{bank or "(empty)"}

Rules:
- must_have_keywords: exact hard skills, tools, frameworks, and requirements the ATS will scan for.
- nice_to_have_keywords: useful but not required.
- missing_keywords: must-haves the source does NOT support. Be honest. Never suggest inventing experience.
- red_flags: things a hiring manager would spot in under 10 seconds (title mismatch, unsupported seniority, unexplained gap, generic summary, keyword stuffing).
- skip_risks: sections of a resume a recruiter is likely to skip for this role, and why.
- angle: one sentence on the strongest truthful positioning for this candidate.
- match_score: 0-100 based only on the source of truth.

Return exactly this JSON and nothing else:
{{
  "match_score": <int 0-100>,
  "must_have_keywords": ["<keyword>"],
  "nice_to_have_keywords": ["<keyword>"],
  "missing_keywords": ["<keyword>"],
  "red_flags": ["<flag>"],
  "skip_risks": ["<risk>"],
  "angle": "<one sentence>"
}}
""".strip()


def generate_jd_brief(
    company: str, role: str, jd_text: str, cfg: Config | None = None
) -> dict:
    cfg = cfg or load_config()
    if not jd_brief_enabled(cfg):
        return {}
    prompt = build_jd_brief_prompt(cfg, company, role, jd_text)
    log.info("Building ATS/recruiter brief for %s — %s", company, role)
    try:
        data = _extract_json_object(complete_prompt(prompt, effort="low"))
        if not data:
            return {}
        try:
            score = max(0, min(100, int(data.get("match_score", 0) or 0)))
        except (TypeError, ValueError):
            score = 0
        return {
            "match_score": score,
            "must_have_keywords": _clean_string_list(data.get("must_have_keywords")),
            "nice_to_have_keywords": _clean_string_list(data.get("nice_to_have_keywords")),
            "missing_keywords": _clean_string_list(data.get("missing_keywords"), 5),
            "red_flags": _clean_string_list(data.get("red_flags"), 3),
            "skip_risks": _clean_string_list(data.get("skip_risks"), 5),
            "angle": str(data.get("angle") or "").strip(),
        }
    except Exception as exc:
        log.error("JD brief failed: %s", exc)
        return {}


def build_voice_critic_prompt(
    jd_text: str,
    tailored_resume: str,
    source_of_truth: str = "",
    cfg: Config | None = None,
) -> str:
    cfg = cfg or load_config()
    threshold = voice_threshold(cfg)
    return f"""
You are a hiring manager reading 200 resumes in one sitting and a line editor who hates AI-sounding writing. Audit the tailored resume against the master CV's voice.

### Job description
{jd_text}

### Source of truth (master CV + experience bank)
{source_of_truth}

### Tailored resume
{tailored_resume}

### What good looks like
{VOICE_CONTRACT}

Score these:
- naturalness (0-100): would a real engineer write this, and does it match the master CV's rhythm, diction, and level of detail? Target: {threshold}+.
- sameness (0-100): how much does it read like a template applied to every JD? 0 = specific to this person and role, 100 = interchangeable.

Flags (use only these labels):
uniform_openers, metric_stuffing, keyword_stuffing, ai_vocabulary, em_dash_overuse, repeated_frames, unsupported_claim, title_inflation, generic_summary.

Also:
- skip_risk: sections a recruiter would skip in 10 seconds, with the reason.
- rewrite_instructions: field-level fixes (SUMMARY, JOB1_B2, SKILL_DATA, etc.). Be specific. Preserve every fact, metric, employer, and date. Do not add keywords or claims.

Return exactly this JSON:
{{
  "naturalness": <int 0-100>,
  "sameness": <int 0-100>,
  "skip_risk": ["<section — reason>"],
  "flags": ["<flag>"],
  "rewrite_instructions": "<specific field-level fixes>"
}}
""".strip()


def evaluate_voice(
    jd_text: str, tailored_resume: str, source_of_truth: str = ""
) -> dict:
    cfg = load_config()
    if not voice_gate_enabled(cfg):
        return {"naturalness": 100, "sameness": 0, "skip_risk": [], "flags": [], "rewrite_instructions": "", "skipped": True}
    prompt = build_voice_critic_prompt(jd_text, tailored_resume, source_of_truth, cfg)
    log.info("Evaluating human voice / 10-second scan")
    try:
        data = _extract_json_object(complete_prompt(prompt, effort="low"))
        if not data:
            return {
                "naturalness": 0,
                "sameness": 100,
                "skip_risk": [],
                "flags": ["generic_summary"],
                "rewrite_instructions": "Failed to parse the voice evaluation.",
                "failed": True,
            }
        try:
            naturalness = max(0, min(100, int(data.get("naturalness", 0) or 0)))
        except (TypeError, ValueError):
            naturalness = 0
        try:
            raw_sameness = data.get("sameness", 100)
            sameness = max(0, min(100, int(100 if raw_sameness is None else raw_sameness)))
        except (TypeError, ValueError):
            sameness = 100
        return {
            "naturalness": naturalness,
            "sameness": sameness,
            "skip_risk": _clean_string_list(data.get("skip_risk")),
            "flags": _clean_string_list(data.get("flags")),
            "rewrite_instructions": str(data.get("rewrite_instructions") or "").strip(),
        }
    except Exception as exc:
        log.error("Voice evaluation failed: %s", exc)
        return {
            "naturalness": 0,
            "sameness": 100,
            "skip_risk": [],
            "flags": [],
            "rewrite_instructions": str(exc),
            "failed": True,
        }


def voice_passes(result: dict | None, cfg: Config | None = None) -> tuple[bool, list[str]]:
    cfg = cfg or load_config()
    if not result or result.get("skipped") or result.get("failed"):
        return True, []
    threshold = voice_threshold(cfg)
    sameness_max = voice_sameness_max(cfg)
    issues = []
    naturalness = int(result.get("naturalness") or 0)
    sameness = int(result.get("sameness") or 0)
    if naturalness < threshold:
        issues.append(f"naturalness {naturalness} < {threshold}")
    if sameness > sameness_max:
        issues.append(f"sameness {sameness} > {sameness_max}")
    critical = sorted(set(result.get("flags") or []) & VOICE_CRITICAL_FLAGS)
    if critical:
        issues.append("flags: " + ", ".join(critical))
    return (not issues, issues)


def build_voice_revision_prompt(
    company: str,
    role: str,
    jd_text: str,
    llm_output: str,
    voice_result: dict,
    jd_brief: dict | None = None,
) -> str:
    return f"""
You are editing an application package for {company} — {role}. Fix the voice problems without changing the facts.

### Original tagged output
{llm_output}

### Voice audit
{json.dumps(voice_result, indent=2)}

### JD brief
{format_jd_brief(jd_brief)}

### Job description
{jd_text}

Rules:
- Rewrite only the flagged fields and any immediate ripple needed for coherence. Leave unflagged fields unchanged.
- Preserve every employer, title, date, metric, technology, and claim from the original and the source of truth. Do not add or invent anything.
- Follow the voice contract. Vary openings and lengths; remove metric stuffing, keyword salad, AI vocabulary, and em dashes.
- Keep the same tagged output format. Return the complete set of tags, not a diff.
""".strip()


def revise_for_voice(
    company: str,
    role: str,
    jd_text: str,
    llm_output: str,
    voice_result: dict,
    jd_brief: dict | None = None,
) -> str:
    prompt = build_voice_revision_prompt(
        company, role, jd_text, llm_output, voice_result, jd_brief
    )
    log.info("Targeted voice revision for %s — %s", company, role)
    try:
        return complete_prompt(prompt, effort="medium")
    except Exception as exc:
        log.error("Voice revision failed: %s", exc)
        return ""


def build_tailor_prompt(
    cfg: Config,
    company: str,
    role: str,
    jd_text: str,
    feedback_history: str = "",
    jd_brief: dict | None = None,
) -> str:
    master = load_optional(cfg.master_cv_path)
    project_mem = load_optional(cfg.root / "memory" / "project.md")
    feedback_mem = load_optional(cfg.root / "memory" / "feedback.md")
    writing_rules = load_optional(cfg.templates_dir / "writing_rules.md")
    feedback_mem = (writing_rules + "\n\n" + feedback_mem).strip()
    bank = load_experience_bank(cfg.experience_bank_dir)
    gen_cover = should_generate_cover_letter(cfg)
    cover_tpl = load_optional(cfg.templates_dir / "cover_letter.template.md") if gen_cover else ""
    dm_tpl = load_optional(cfg.templates_dir / "linkedin_dm.template.md")
    why_tpl = load_optional(cfg.templates_dir / "why_i_fit.template.md")
    visa = cfg.get("visa.description") or cfg.get("visa.status") or ""
    signoff = cfg.get("outreach.signoff") or f"— {cfg.preferred_name}"
    dm_words = cfg.get("outreach.linkedin_dm_max_words", 60)
    level = fabrication_freedom(cfg)
    ats_threshold = int(cfg.get("pipeline.ats_threshold", 80) or 80)

    cover_tpl_block = f"Cover letter template:\n{cover_tpl}\n\n" if gen_cover and cover_tpl else ""
    cover_instruction = (
        f"- Cover letter: 250-400 words, cite something specific about {company} if the JD contains it; otherwise stay concrete about the role. Include the visa line if relevant.\n"
        if gen_cover
        else ""
    )
    titles = approved_titles(cfg)
    titles_block = "\n".join(f"  {i + 1}. {title}" for i, title in enumerate(titles))
    brief_block = format_jd_brief(jd_brief)

    return f"""
You are tailoring application materials for {cfg.full_name} applying to '{role}' at {company}.

### Job description
{jd_text}

### JD brief (ATS + recruiter read — prioritization only)
{brief_block}
Treat this as a priority list, not a checklist to stuff. The master CV and experience bank still control what is true.

### Master CV (source of truth — do not contradict employers/dates/education)
{master}

### Experience bank (Source Material — do NOT copy directly; synthesize highly targeted bullets)
{bank or "(empty — use the master CV only)"}

### Profile / visa
{project_mem}
Visa: {visa}

### Writing rules (must follow; fabrication limits come from FABRICATION FREEDOM below, not from inventing freely)
{feedback_mem}

{VOICE_CONTRACT}

### Templates & Outreach
{cover_tpl_block}LinkedIn DM template:
{dm_tpl}

Why I fit template:
{why_tpl}

Sign-off: {signoff}
LinkedIn DM max words: {dm_words}

{freedom_instructions(level, pages=cfg.cv_pages, ats_threshold=ats_threshold)}

{_retry_section(feedback_history, cfg)}

### INSTRUCTIONS
{tailor_layout_instructions(cfg)}
- Classify the JD into a role type. Rewrite the strongest evidence from the master CV and experience bank in the master CV's voice. Do not copy bullets verbatim, and do not flatten every bullet into the same shape. Focus on what the candidate individually owned, coded, and delivered. {_bullet_count_instruction(cfg)}
- If the bank has fewer bullets than needed, fill the rest from the master CV within the freedom level above.
- Text changes only. Do not add/remove jobs, projects, education, or employers from the template.
- Rewrite the tagline and summary for this role. The tagline MUST be picked EXACTLY from this pre-approved list (pick the one that best matches the JD):
{titles_block}
  DO NOT invent your own title. NEVER append buzzwords or domain specifics outside of this list.
- KEYWORD PLACEMENT: Put the JD's hard keywords in Key Skills first. Use a JD term in a bullet only where the candidate's verified experience supports it and the sentence still reads naturally. Do not repeat a keyword across bullets to raise ATS density.
- BULLET VARIETY: Vary openings, lengths, and shapes. Do not turn every bullet into a metric. At most one numeric metric per bullet, and only when the source contains that number. At least one bullet per employer should explain a decision, constraint, or tradeoff rather than an outcome.
- SUMMARY: Write a 2-3 sentence prose paragraph in the master CV's voice. Do not build it from semicolon-separated keyword lists or em dashes.
- Key skills: Reorder each skills list so JD-relevant items come first. What you may add is governed by FABRICATION FREEDOM above.
- Job title lines: YOU MUST USE THE EXACT EMPLOYER TITLES FROM THE MASTER CV. Do not alter past job titles to match the JD (e.g., if you were a 'Backend Developer', do not change it to 'Data Engineer' even if the JD asks for it).
- Do not include dates in TITLE tags (dates are already in the HTML template).
{cover_instruction}- LinkedIn DM: ≤{dm_words} words, no emoji, no "hope this finds you well".
- Why I fit: exactly 3 bullets, each ≤25 words, each tied to one JD requirement using evidence allowed at the current freedom level.
- Hard page budget: the rendered PDF MUST fit `cv_format.pages` ({cfg.cv_pages}). Prefer fewer / shorter bullets over overflow.
- Keep reasoning tags (<R_...>) ultra-concise (1 brief sentence or clause max) to conserve tokens and preserve output fidelity.

### OUTPUT FORMAT
Return ONLY these tagged blocks. For every content tag except ROLE_TYPE and ANALYSIS, put <R_TAG> immediately before it.

{_tag_schema(cfg)}
""".strip()



def generate_tailored_materials(
    company: str,
    role: str,
    jd_text: str,
    feedback_history: str = "",
    jd_brief: dict | None = None,
) -> str:
    prompt = build_tailor_prompt(
        load_config(), company, role, jd_text, feedback_history, jd_brief
    )
    log.info("Tailoring materials for %s — %s", company, role)
    try:
        return complete_prompt(prompt, effort="high")
    except Exception as exc:
        log.error("Tailor LLM failed: %s", exc)
        return ""


def parse_tagged_output(output: str, cfg: Config | None = None) -> dict:
    if "<TITLE>" in output:
        # Keep reasoning that sits immediately above the last TITLE block.
        last = output.rfind("<R_TITLE>")
        if last != -1:
            output = output[last:]
        else:
            output = "<TITLE>" + output.split("<TITLE>")[-1]

    result = {}
    for tag in all_tags(cfg):
        matches = re.findall(rf"<{tag}>(.*?)</{tag}>", output, re.DOTALL)
        result[tag] = matches[-1].strip() if matches else ""
        r_matches = re.findall(rf"<R_{tag}>(.*?)</R_{tag}>", output, re.DOTALL)
        result[f"R_{tag}"] = r_matches[-1].strip() if r_matches else ""
    return result


def strip_skill_prefix(text: str, label: str) -> str:
    text = text.strip()
    pattern = rf"^{re.escape(label)}\s*:\s*"
    text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    if label == "AI & Agentic Systems":
        text = re.sub(r"^ML\s*&\s*AI\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    elif label == "Cloud-Native & DevOps":
        text = re.sub(r"^Cloud\s*&\s*DevOps\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    return text


def strip_title_dates(text: str) -> str:
    cleaned = re.split(r"[\t]| {2,}", text, maxsplit=1)[0].strip()
    cleaned = re.sub(r"\s*\[optional honest descriptor[^\]]*\]", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def validate_tailored_output(parsed: dict, cfg: Config | None = None) -> tuple[bool, list[str]]:
    """Strictly validate that tailored output contains all required sections and is not truncated."""
    cfg = cfg or load_config()
    errors: list[str] = []

    # 1. Headline & Summary
    title = (parsed.get("TITLE") or "").strip()
    if not title or "{{" in title or "[optional" in title.lower():
        errors.append("TITLE missing, empty, or contains placeholder text")
    summary = (parsed.get("SUMMARY") or "").strip()
    if not summary or len(summary) < 40 or "{{" in summary:
        errors.append("SUMMARY missing, too short (< 40 chars), or contains placeholder text")

    # 2. Every employer must have a title and at least 2 bullets
    for job in job_blocks(cfg):
        prefix = job["prefix"]
        employer = job["employer"]
        job_title = (parsed.get(f"{prefix}_TITLE") or "").strip()
        if not job_title or "{{" in job_title or "[optional" in job_title.lower():
            errors.append(f"{employer} ({prefix}_TITLE) missing or contains placeholder text")
        bullets = [
            (parsed.get(f"{prefix}_B{i}") or "").strip()
            for i in range(1, job["bullets"] + 1)
            if (parsed.get(f"{prefix}_B{i}") or "").strip()
        ]
        min_bullets = max(2, int(job.get("bullets_min", 4) or 4))
        if len(bullets) < min_bullets:
            errors.append(f"{employer} has only {len(bullets)} bullet(s) (minimum {min_bullets} required)")

    # 3. Key Skills sections
    for name, label in SKILL_BLOCKS:
        skills = (parsed.get(name) or "").strip()
        if not skills or "{{" in skills or len(skills) < 5:
            errors.append(f"Key skill block {name} ({label}) missing or empty")

    # 4. Cover letter & supporting materials
    if should_generate_cover_letter(cfg):
        cover = (parsed.get("COVER_LETTER") or "").strip()
        if not cover or len(cover.split()) < 80:
            errors.append("COVER_LETTER missing or too short (< 80 words)")

    dm = (parsed.get("LINKEDIN_DM") or "").strip()
    if not dm or len(dm.split()) < 10:
        errors.append("LINKEDIN_DM missing or too short (< 10 words)")

    # 5. Strict formatting and tone rules
    for k, v in parsed.items():
        if isinstance(v, str):
            if "--" in v:
                errors.append(f"Strict rule violation: {k} contains double hyphens '--'")

    # 6. Personalization validation
    for text_field in ["COVER_LETTER"]:
        val = (parsed.get(text_field) or "").strip()
        if val and not re.search(r'\b(I|my|me|mine|I\'ve|I\'m)\b', val, re.IGNORECASE):
            errors.append(f"{text_field} lacks personalization (missing I/my/me/mine)")

    return len(errors) == 0, errors


def normalize_parsed(parsed: dict, cfg: Config | None = None) -> dict:
    out = dict(parsed)
    for name, label in SKILL_BLOCKS:
        if out.get(name):
            out[name] = strip_skill_prefix(out[name], label)
    for job in job_blocks(cfg):
        key = f"{job['prefix']}_TITLE"
        if out.get(key):
            out[key] = strip_title_dates(out[key])
    return out


def resume_plain_text(parsed: dict, cfg: Config | None = None) -> str:
    lines = [parsed.get("TITLE", ""), parsed.get("SUMMARY", ""), ""]
    for job in job_blocks(cfg):
        lines.append(job["employer"])
        lines.append(parsed.get(f"{job['prefix']}_TITLE", ""))
        for i in range(1, job["bullets"] + 1):
            bullet = parsed.get(f"{job['prefix']}_B{i}", "")
            if bullet:
                lines.append(f"- {bullet}")
        lines.append("")
    lines.append("Skills")
    for name, label in SKILL_BLOCKS:
        value = parsed.get(name, "")
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines).strip()


def build_critic_prompt(
    jd_text: str,
    tailored_resume: str,
    source_of_truth: str,
    cfg: Config | None = None,
) -> str:
    cfg = cfg or load_config()
    level = fabrication_freedom(cfg)
    ats_threshold = int(cfg.get("pipeline.ats_threshold", 80) or 80)
    score_cap = (
        "overall score should reflect ATS readiness; do not force score <= honesty when "
        "freedom level explicitly allows fabrication beyond the source."
        if level >= 4
        else "overall score MUST be <= honesty."
    )
    return f"""
You are a rigorous ATS auditor and truthfulness verifier. Score keyword coverage and honesty for fabrication freedom level {level}/5.

### Job description
{jd_text}

### Source of truth (master CV + experience bank)
{source_of_truth}

### Tailored resume
{tailored_resume}

Rules:
- Target ATS score threshold: {ats_threshold}. Critique should help the writer reach it within freedom level {level}.
- keyword_coverage (0-100): evaluate exact match of required hard skills, tools, frameworks, and architectural domain concepts from the JD.
- honesty (0-100): score relative to freedom level {level} (see below), not absolute literalism when higher freedom is set.
- {score_cap}
- Hard page budget reminder: critique may require dropping bullets to fit {cfg.cv_pages} page(s).
{critic_freedom_rules(level)}
- gaps: list specific missing hard skills, libraries, frameworks, or domain keywords from the JD that are not yet covered.
- critique: provide 2-3 specific, actionable recommendations:
  1. Exact missing JD keywords from 'gaps' to weave into Key Skills or bullets within freedom {level}.
  2. Any honesty violations (unearned technologies or metrics) that must be removed/softened.
  3. Guidance on protecting the {cfg.cv_pages}-page budget.

Return exactly this JSON:
{{
  "score": <int 0-100>,
  "keyword_coverage": <int 0-100>,
  "honesty": <int 0-100>,
  "gaps": ["<gap>"],
  "critique": "<actionable remediation instructions for freedom {level}>"
}}
""".strip()


def evaluate_ats_score(jd_text: str, tailored_resume: str, source_of_truth: str = "") -> dict:
    cfg = load_config()
    prompt = build_critic_prompt(jd_text, tailored_resume, source_of_truth, cfg)
    log.info("Evaluating keyword coverage + honesty (freedom=%s)", fabrication_freedom(cfg))
    try:
        out = complete_prompt(prompt, effort="low")
        match = re.search(r"\{.*\}", out, re.DOTALL)
        if not match:
            return {"score": 0, "honesty": 0, "critique": "Failed to parse JSON evaluation."}
        data = json.loads(match.group(0))
        honesty = int(data.get("honesty", 0) or 0)
        score = int(data.get("score", 0) or 0)
        data["honesty"] = honesty
        level = fabrication_freedom(cfg)
        # At low freedom, score cannot exceed honesty (including honesty=0 → score 0).
        # At 4–5, ATS score can stand alone so fabrication is not honesty-capped.
        data["score"] = score if level >= 4 else min(score, honesty)
        data["fabrication_freedom"] = level
        return data
    except Exception as exc:
        log.error("Evaluation failed: %s", exc)
        return {"score": 0, "honesty": 0, "critique": str(exc)}


def source_of_truth_text(cfg: Config) -> str:
    master = load_optional(cfg.master_cv_path)
    bank = load_experience_bank(cfg.experience_bank_dir)
    return f"{master}\n\n{bank}".strip()


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_phone(raw: str, dashed: bool = True) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if dashed and len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:]}"
    return str(raw or "").strip()


def contact_line_html(cfg: Config) -> str:
    city = cfg.get("user.city") or ""
    country = cfg.get("user.country") or ""
    location = ", ".join(p for p in (city, country) if p)
    raw_phone = str(cfg.get("user.phone") or "")
    dashed = str(cfg.get("cv_format.phone_format", "dashed")).lower() != "raw"
    phone = format_phone(raw_phone, dashed=dashed)
    email = cfg.get("user.email") or ""
    linkedin = cfg.get("user.linkedin") or ""
    github = cfg.get("user.github") or ""
    link_style = str(cfg.get("cv_format.contact_links", "labels")).lower()

    def display_url(url: str) -> str:
        return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")

    bits = []
    if location:
        bits.append(f'<span>{escape_html(location)}</span>')
    if phone:
        bits.append(f'<span>{escape_html(phone)}</span>')
    if email:
        bits.append(f'<a href="mailto:{escape_html(email)}">{escape_html(email)}</a>')
    if linkedin:
        label = "LinkedIn" if link_style != "urls" else display_url(linkedin)
        bits.append(f'<a href="{escape_html(linkedin)}">{escape_html(label)}</a>')
    if github:
        label = "GitHub" if link_style != "urls" else display_url(github)
        bits.append(f'<a href="{escape_html(github)}">{escape_html(label)}</a>')
    return '<span class="dot" aria-hidden="true">·</span>'.join(bits)


def apply_changes_to_html(parsed: dict, output_path: Path, cfg: Config | None = None) -> list[dict]:
    cfg = cfg or load_config()
    html_content = cfg.html_template_path.read_text()
    html_content = apply_cv_format(html_content, cfg)
    html_content = ensure_bullet_slots(html_content, job_blocks(cfg))
    html_content = html_content.replace("{{FULL_NAME}}", escape_html(cfg.full_name))
    html_content = html_content.replace("{{CONTACT_LINE}}", contact_line_html(cfg))

    changes = []
    parsed = normalize_parsed(parsed, cfg)
    for tag in resume_tags(cfg):
        new_text = (parsed.get(tag) or "").strip()
        if not new_text:
            if not _is_job_bullet_tag(tag):
                log.warning("No content for tag <%s> — leaving placeholder", tag)
            continue
        reasoning = parsed.get(f"R_{tag}") or "No specific reasoning provided."
        changes.append({"tag": tag, "new": new_text, "reasoning": reasoning})
        html_content = html_content.replace(f"{{{{{tag}}}}}", escape_html(new_text))

    html_content = strip_unused_bullet_placeholders(html_content)
    remaining = re.findall(r"\{\{([A-Z0-9_]+)\}\}", html_content)
    if remaining:
        raise ValueError(
            f"Tailored HTML contains unreplaced placeholders: {', '.join(sorted(set(remaining)))}"
        )

    output_path.write_text(html_content)
    log.info("Saved tailored HTML: %s", output_path)
    return changes


def write_changes_file(path: Path, changes: list[dict], eval_result: dict | None = None, feedback_history: str = "") -> None:
    lines = ["# Resume Tailoring Changes", ""]
    if not changes:
        lines.append("No text changes were made.")
    for item in changes:
        lines.append(f"### {item['tag']}")
        lines.append(f"**Reasoning:** _{item['reasoning']}_")
        lines.append("")
        lines.append(f"**Tailored:** {item['new']}")
        lines.append("")
        lines.append("---")
        lines.append("")
    if eval_result:
        lines.append("# Evaluation")
        lines.append(f"**Score:** {eval_result.get('score', 0)}/100")
        lines.append(f"**Honesty:** {eval_result.get('honesty', 'n/a')}/100")
        lines.append(f"**Keyword coverage:** {eval_result.get('keyword_coverage', 'n/a')}/100")
        if eval_result.get("voice_score") is not None:
            lines.append(f"**Voice:** {eval_result.get('voice_score', 'n/a')}/100")
            lines.append(f"**Sameness:** {eval_result.get('sameness', 'n/a')}/100")
            voice = eval_result.get("voice") or {}
            if voice.get("flags"):
                lines.append(f"**Voice flags:** {', '.join(voice['flags'])}")
            if voice.get("rewrite_instructions"):
                lines.append(f"**Voice fixes:** {voice['rewrite_instructions']}")
        lines.append(f"**Critique:** {eval_result.get('critique', '')}")
        gaps = eval_result.get("gaps") or []
        if gaps:
            lines.append("")
            lines.append("**Honest gaps:**")
            for gap in gaps:
                lines.append(f"- {gap}")
        try:
            from pipeline.verify import verification_note_lines

            verify_lines = verification_note_lines(eval_result)
        except Exception:
            verify_lines = []
        if verify_lines:
            lines.append("")
            lines.extend(verify_lines)
        if feedback_history:
            lines.append("")
            lines.append("**Retry history:**")
            lines.append("```")
            lines.append(feedback_history.strip())
            lines.append("```")
        lines.append("")
    path.write_text("\n".join(lines))
    log.info("Saved changes diff: %s", path)


LETTER_HEIGHT_PX = 11 * 96
_LI_ITEM_RE = re.compile(r"[ \t]*<li\b[^>]*>.*?</li>\s*", re.IGNORECASE | re.DOTALL)


def pdf_scale(content_height_px: float, max_pages: int, page_h_px: float = LETTER_HEIGHT_PX) -> float:
    """Never shrink type to hit a multi-page target. Only shrink when pages == 1 and content overflows."""
    max_pages = max(1, int(max_pages))
    if max_pages > 1:
        return 1.0
    limit = page_h_px
    if content_height_px <= limit:
        return 1.0
    return min(1.0, (limit / content_height_px) * 0.99)


def _trim_one_overflow_bullet(html: str):
    """Remove the last <li> from the longest experience/project list. Returns None if nothing trimmed."""
    uls = list(re.finditer(r"<ul\b[^>]*>.*?</ul>", html, flags=re.IGNORECASE | re.DOTALL))
    if not uls:
        return None
    best = None
    best_count = 0
    for match in uls:
        items = list(_LI_ITEM_RE.finditer(match.group(0)))
        if len(items) > best_count:
            best = (match, items)
            best_count = len(items)
    if not best or best_count < 2:
        return None
    ul_match, items = best
    last = items[-1]
    new_ul = ul_match.group(0)[: last.start()] + ul_match.group(0)[last.end() :]
    return html[: ul_match.start()] + new_ul + html[ul_match.end() :]


async def html_to_pdf(html_path: Path, pdf_path: Path, max_pages: int | None = None) -> None:
    from playwright.async_api import async_playwright

    cfg = load_config()
    max_pages = int(max_pages) if max_pages is not None else cfg.cv_pages
    max_pages = max(1, max_pages)
    html_path = Path(html_path)
    height = page_height_px(cfg)
    page_budget = height * max_pages

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()

        for trim_round in range(8):
            file_url = "file://" + os.path.abspath(html_path)
            await page.goto(file_url, wait_until="networkidle")
            await page.emulate_media(media="print")
            await page.evaluate("() => document.fonts.ready")
            body_height = float(await page.evaluate("() => document.documentElement.scrollHeight"))
            if body_height <= page_budget or max_pages == 1:
                break
            html = html_path.read_text()
            trimmed = _trim_one_overflow_bullet(html)
            if not trimmed or trimmed == html:
                log.warning(
                    "HTML height %.0fpx still exceeds cv_format.pages=%s (%.0fpx); no more bullets to trim.",
                    body_height,
                    max_pages,
                    page_budget,
                )
                break
            html_path.write_text(trimmed)
            log.info(
                "Trimmed a bullet to fit cv_format.pages=%s (height %.0fpx > %.0fpx, round %s)",
                max_pages,
                body_height,
                page_budget,
                trim_round + 1,
            )

        body_height = float(await page.evaluate("() => document.documentElement.scrollHeight"))
        scale = pdf_scale(body_height, max_pages, height)
        if body_height > page_budget and max_pages > 1:
            log.warning(
                "HTML height %.0fpx still exceeds cv_format.pages=%s (%.0fpx) after trim.",
                body_height,
                max_pages,
                page_budget,
            )
        log.info("PDF scale factor: %.3f (cv_format.pages=%s)", scale, max_pages)
        await page.pdf(
            path=str(pdf_path),
            format=page_size(cfg),
            print_background=True,
            prefer_css_page_size=True,
            scale=scale,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        await browser.close()

    try:
        from pypdf import PdfReader

        page_count = len(PdfReader(str(pdf_path)).pages)
    except Exception:
        page_count = None
    if page_count is not None and page_count > max_pages:
        log.warning(
            "PDF is %s pages; cv_format.pages=%s. Tighten spacing or drop a bullet — not shrinking type.",
            page_count,
            max_pages,
        )
    elif page_count is not None:
        log.info("PDF page count: %s (cv_format.pages=%s)", page_count, max_pages)


async def rebuild_package_pdf(folder: Path) -> Path:
    """Re-render PDF (and Word/Pages) from the HTML already on disk."""
    folder = Path(folder)
    html_out = next(iter(sorted(folder.glob("*_CV.html"))), None)
    if html_out is None or not html_out.is_file():
        raise FileNotFoundError(f"No *_CV.html in {folder.name}")
    pdf_out = html_out.with_suffix(".pdf")
    await html_to_pdf(html_out, pdf_out)
    log.info("Rebuilt PDF from HTML: %s", pdf_out)
    try:
        from pipeline.cv_export import write_editable_exports

        write_editable_exports(html_out, pdf_out if pdf_out.is_file() else None)
    except Exception as exc:
        log.warning("Word/Pages export failed: %s", exc)
    return pdf_out


async def save_materials(
    company: str,
    role: str,
    llm_output: str,
    eval_result: dict | None = None,
    feedback_history: str = "",
    date_str: str | None = None,
    job: dict | None = None,
    jd_brief: dict | None = None,
    on_progress=None,
) -> Path:
    def note(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    cfg = load_config()
    date_str = date_str or datetime.date.today().isoformat()
    from pipeline.search import unique_application_dir

    output_dir = unique_application_dir(
        cfg, company, role, date_str, (job or {}).get("url") or ""
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = normalize_parsed(parse_tagged_output(llm_output))
    (output_dir / "llm_output_raw.txt").write_text(llm_output)

    html_out = output_dir / f"{cfg.cv_stem}.html"
    changes = apply_changes_to_html(parsed, html_out, cfg)
    if jd_brief:
        (output_dir / "jd_brief.json").write_text(json.dumps(jd_brief, indent=2))
    write_changes_file(
        output_dir / f"{cfg.cv_stem}_changes.md",
        changes,
        eval_result=eval_result,
        feedback_history=feedback_history,
    )
    if eval_result:
        payload = dict(eval_result)
        if feedback_history:
            payload["retry_history"] = feedback_history.strip()
        (output_dir / "evaluation.json").write_text(json.dumps(payload, indent=2))

    loc = ((job or {}).get("location") or "").strip()
    from pipeline.jobs import infer_work_mode

    work_mode = ((job or {}).get("work_mode") or "").strip().lower() or infer_work_mode(
        loc, (job or {}).get("jd") or ""
    )
    meta = {
        "company": company,
        "role": role,
        "url": (job or {}).get("url") or "",
        "location": loc,
        "work_mode": work_mode,
        "source": (job or {}).get("source") or (job or {}).get("channel") or "",
        "apply_url": (job or {}).get("apply_url") or "",
        "apply_kind": (job or {}).get("apply_kind") or "",
        "jd": (job or {}).get("jd") or jd_text,
    }
    if (job or {}).get("fabrication_freedom") is not None:
        meta["fabrication_freedom"] = job.get("fabrication_freedom")
        meta["fabrication_freedom_mode"] = job.get("fabrication_freedom_mode") or ""
        meta["fabrication_freedom_reason"] = job.get("fabrication_freedom_reason") or ""
    (output_dir / "job.json").write_text(json.dumps(meta, indent=2))

    (output_dir / "cover_letter.md").write_text(parsed.get("COVER_LETTER") or "")
    (output_dir / "linkedin_dm.txt").write_text(parsed.get("LINKEDIN_DM") or "")
    (output_dir / "why_i_fit.txt").write_text(parsed.get("WHY_I_FIT") or "")

    analysis = parsed.get("ANALYSIS") or ""
    role_type = parsed.get("ROLE_TYPE") or ""
    analysis_body = [f"# JD Analysis — {company} / {role}", ""]
    if role_type:
        analysis_body.append(f"**Role type:** {role_type}")
        analysis_body.append("")
    if jd_brief:
        score = jd_brief.get("match_score")
        if score is not None:
            analysis_body.append(f"**JD fit score:** {score}/100")
        angle = str(jd_brief.get("angle") or "").strip()
        if angle:
            analysis_body.append(f"**Angle:** {angle}")
        try:
            from pipeline.verify import skill_gap_note_lines

            gap_lines = skill_gap_note_lines(jd_brief)
        except Exception:
            gap_lines = []
        analysis_body.extend(gap_lines)
        if score is not None or angle or gap_lines:
            analysis_body.append("")
    analysis_body.append(analysis)
    (output_dir / "analysis.md").write_text("\n".join(analysis_body).strip() + "\n")

    pdf_out = output_dir / f"{cfg.cv_stem}.pdf"
    note("Building PDF")
    try:
        await html_to_pdf(html_out, pdf_out)
        log.info("Compiled PDF: %s", pdf_out)
    except Exception as exc:
        log.error("PDF conversion failed: %s", exc)

    note("Exporting Word/Pages")
    try:
        from pipeline.cv_export import write_editable_exports

        write_editable_exports(html_out, pdf_out if pdf_out.exists() else None)
    except Exception as exc:
        log.warning("Word/Pages export failed: %s", exc)

    return output_dir
