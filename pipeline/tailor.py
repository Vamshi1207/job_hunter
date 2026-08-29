"""Tailor an HTML CV from the master CV + experience bank. Never invent."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from pipeline.bank import load_experience_bank, load_optional
from pipeline.config import Config, load_config
from pipeline.cv_format import apply_cv_format, page_height_px, page_size, tailor_layout_instructions
from pipeline.jobs import slug

log = logging.getLogger(__name__)

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
        "default_title": "Data Engineer",
        "bullets": 4,
    },
    {
        "prefix": "JOB3",
        "employer": "Example Three",
        "default_title": "Software Engineer",
        "bullets": 6,
    },
]


def job_blocks(cfg: Config | None = None) -> list[dict]:
    """Employer blocks come from config.yaml `experience.jobs` so personal employers stay local."""
    cfg = cfg or load_config()
    raw = cfg.get("experience.jobs")
    if isinstance(raw, list):
        out = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("prefix"):
                continue
            try:
                bullets = max(1, int(item.get("bullets") or 4))
            except (TypeError, ValueError):
                bullets = 4
            out.append(
                {
                    "prefix": str(item["prefix"]).upper().replace(" ", ""),
                    "employer": str(item.get("employer") or item["prefix"]),
                    "default_title": str(item.get("default_title") or "Software Engineer"),
                    "bullets": bullets,
                }
            )
        if out:
            return out
    return [dict(block) for block in DEFAULT_JOB_BLOCKS]


SKILL_BLOCKS = [
    ("SKILL_LANG", "Programming languages"),
    ("SKILL_ML", "ML & AI"),
    ("SKILL_DATA", "Distributed systems & data"),
    ("SKILL_BACKEND", "Backend & APIs"),
    ("SKILL_CLOUD", "Cloud & DevOps"),
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
    return resume_tags(cfg) + EXTRA_TAGS


def call_agy(prompt: str, effort: str = "high") -> str:
    cfg = load_config()
    model = cfg.get("pipeline.model", "gemini-3.1-pro")
    agy = shutil.which("agy") or "/root/.local/bin/agy"
    result = subprocess.run(
        [agy, "--print", prompt, "--model", model, "--effort", effort],
        capture_output=True,
        text=True,
        check=True,
        timeout=int(cfg.get("pipeline.llm_timeout_seconds", 600)),
    )
    return result.stdout


def _tag_schema(cfg: Config | None = None) -> str:
    lines = [
        "<R_TITLE>why this tagline</R_TITLE>",
        "<TITLE>one-line tagline matching the role (no name, no location, no inflated seniority)</TITLE>",
        "<R_SUMMARY>why this summary / which about-variant</R_SUMMARY>",
        "<SUMMARY>2-3 sentence summary grounded in the master CV</SUMMARY>",
        "",
    ]
    for job in job_blocks(cfg):
        prefix = job["prefix"]
        lines.append(f"<R_{prefix}_TITLE>why this title wording</R_{prefix}_TITLE>")
        lines.append(
            f"<{prefix}_TITLE>{job['default_title']} [optional honest descriptor, no dates]</{prefix}_TITLE>"
        )
        for i in range(1, job["bullets"] + 1):
            lines.append(f"<R_{prefix}_B{i}>why this bullet / which bank variant</R_{prefix}_B{i}>")
            lines.append(f"<{prefix}_B{i}>bullet text</{prefix}_B{i}>")
        lines.append("")
    for name, label in SKILL_BLOCKS:
        lines.append(f"<R_{name}>why this order</R_{name}>")
        lines.append(f"<{name}>comma-separated list only — do not repeat '{label}:'</{name}>")
    lines.extend(
        [
            "",
            "<R_COVER_LETTER>cover letter angle</R_COVER_LETTER>",
            "<COVER_LETTER>250-400 word cover letter</COVER_LETTER>",
            "<R_LINKEDIN_DM>why this hook</R_LINKEDIN_DM>",
            "<LINKEDIN_DM>≤60 word cold DM</LINKEDIN_DM>",
            "<R_WHY_I_FIT>which JD requirements these map to</R_WHY_I_FIT>",
            "<WHY_I_FIT>exactly 3 bullets, each ≤25 words</WHY_I_FIT>",
            "<ROLE_TYPE>primary role type (e.g. Engineering, Data, FDE, Platform)</ROLE_TYPE>",
            "<ANALYSIS>short markdown: keywords, strongest evidence, honest gaps, bullets selected from bank</ANALYSIS>",
        ]
    )
    return "\n".join(lines)


def _retry_section(feedback_history: str) -> str:
    if not feedback_history:
        return ""
    return f"""
### PREVIOUS ATTEMPT FEEDBACK
{feedback_history}

You may ONLY: reorder skills, swap in a closer experience-bank variant, inject JD wording for skills already in the master CV or bank, or tighten the summary.
You MUST NOT invent technologies, domains, employers, job titles, metrics, or responsibilities.
If a JD requirement cannot be met honestly, leave it as a gap.
"""


def build_tailor_prompt(cfg: Config, company: str, role: str, jd_text: str, feedback_history: str = "") -> str:
    master = load_optional(cfg.master_cv_path)
    project_mem = load_optional(cfg.root / "memory" / "project.md")
    feedback_mem = load_optional(cfg.root / "memory" / "feedback.md")
    bank = load_experience_bank(cfg.experience_bank_dir)
    cover_tpl = load_optional(cfg.templates_dir / "cover_letter.template.md")
    dm_tpl = load_optional(cfg.templates_dir / "linkedin_dm.template.md")
    why_tpl = load_optional(cfg.templates_dir / "why_i_fit.template.md")
    visa = cfg.get("visa.description") or cfg.get("visa.status") or ""
    signoff = cfg.get("outreach.signoff") or f"— {cfg.preferred_name}"
    dm_words = cfg.get("outreach.linkedin_dm_max_words", 60)

    return f"""
You are tailoring application materials for {cfg.full_name} applying to '{role}' at {company}.

### Job description
{jd_text}

### Master CV (source of truth — do not contradict)
{master}

### Experience bank (pick matching role-type variants; fill remaining bullets from the master CV)
{bank or "(empty — use the master CV only)"}

### Profile / visa
{project_mem}
Visa: {visa}

### Writing rules (must follow)
{feedback_mem}

### Cover letter / DM / why-I-fit templates
Cover letter template:
{cover_tpl}

LinkedIn DM template:
{dm_tpl}

Why I fit template:
{why_tpl}

Sign-off: {signoff}
LinkedIn DM max words: {dm_words}

{_retry_section(feedback_history)}

### INSTRUCTIONS
{tailor_layout_instructions(cfg)}
- Classify the JD into a role type, then SELECT bullets from the experience bank whose target matches. Keep the exact bullet counts: {", ".join(f"{j['employer']} {j['bullets']}" for j in job_blocks(cfg))}.
- If the bank has fewer bullets than required, fill the rest from the master CV. Never pad with invented work.
- You MUST NOT invent technologies, domains, employers, job titles, metrics, or responsibilities.
- Text changes only. Do not add/remove jobs, projects, education, or employers.
- Inject JD keywords only where they describe work the candidate actually did.
- Rewrite the tagline and summary for this role. Summary must stay interview-defensible.
- Reorder each skills list so JD-relevant items the candidate already has come first. Do not add skills that are absent from the master CV.
- Job title lines: honest titles only. Do not use Staff / Senior Staff / Principal unless those are the actual titles in the master CV.
- Do not include dates in TITLE tags (dates are already in the HTML template).
- Cover letter: 250-400 words, cite something specific about {company} if the JD contains it; otherwise stay concrete about the role. Include the visa line if relevant.
- LinkedIn DM: ≤{dm_words} words, no emoji, no "hope this finds you well".
- Why I fit: exactly 3 bullets, each ≤25 words, each tied to one JD requirement using verified evidence.

### OUTPUT FORMAT
Return ONLY these tagged blocks. For every content tag except ROLE_TYPE and ANALYSIS, put <R_TAG> immediately before it.

{_tag_schema(cfg)}
""".strip()


def generate_tailored_materials(company: str, role: str, jd_text: str, feedback_history: str = "") -> str:
    cfg = load_config()
    prompt = build_tailor_prompt(cfg, company, role, jd_text, feedback_history)
    log.info("Tailoring materials for %s — %s", company, role)
    try:
        return call_agy(prompt, effort="high")
    except subprocess.CalledProcessError as exc:
        log.error("agy failed: %s", exc.stderr)
        return ""
    except FileNotFoundError:
        log.error("agy CLI not found on PATH")
        return ""


def parse_tagged_output(output: str) -> dict:
    if "<TITLE>" in output:
        # Keep reasoning that sits immediately above the last TITLE block.
        last = output.rfind("<R_TITLE>")
        if last != -1:
            output = output[last:]
        else:
            output = "<TITLE>" + output.split("<TITLE>")[-1]

    result = {}
    for tag in all_tags():
        matches = re.findall(rf"<{tag}>(.*?)</{tag}>", output, re.DOTALL)
        result[tag] = matches[-1].strip() if matches else ""
        r_matches = re.findall(rf"<R_{tag}>(.*?)</R_{tag}>", output, re.DOTALL)
        result[f"R_{tag}"] = r_matches[-1].strip() if r_matches else ""
    return result


def strip_skill_prefix(text: str, label: str) -> str:
    text = text.strip()
    pattern = rf"^{re.escape(label)}\s*:\s*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def strip_title_dates(text: str) -> str:
    return re.split(r"[\t]| {2,}", text, maxsplit=1)[0].strip()


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


def build_critic_prompt(jd_text: str, tailored_resume: str, source_of_truth: str) -> str:
    return f"""
You are a resume auditor. Score keyword coverage and honesty. You are not a keyword stuffer.

### Job description
{jd_text}

### Source of truth (master CV + experience bank)
{source_of_truth}

### Tailored resume
{tailored_resume}

Rules:
- keyword_coverage (0-100): how well the tailored resume surfaces existing experience that matches the JD.
- honesty (0-100): 100 means every claim is supported by the source of truth. Deduct for new technologies, domains, employers, titles, or metrics.
- overall score MUST be <= honesty.
- NEVER tell the writer to invent, fabricate, or add experience the candidate does not have.
- If the JD requires something absent, list it under gaps and suggest adjacent-evidence framing or an honest omission.
- allowed_fixes may only be: reorder skills, swap experience-bank variants, inject JD wording for skills already in the source of truth, tighten summary.

Return exactly this JSON:
{{
  "score": <int 0-100, <= honesty>,
  "keyword_coverage": <int 0-100>,
  "honesty": <int 0-100>,
  "gaps": ["<honest gap>"],
  "critique": "<what to change using only allowed_fixes>"
}}
""".strip()


def evaluate_ats_score(jd_text: str, tailored_resume: str, source_of_truth: str = "") -> dict:
    prompt = build_critic_prompt(jd_text, tailored_resume, source_of_truth)
    log.info("Evaluating keyword coverage + honesty")
    try:
        out = call_agy(prompt, effort="low")
        match = re.search(r"\{.*\}", out, re.DOTALL)
        if not match:
            return {"score": 0, "honesty": 0, "critique": "Failed to parse JSON evaluation."}
        data = json.loads(match.group(0))
        honesty = int(data.get("honesty", 0) or 0)
        score = int(data.get("score", 0) or 0)
        data["honesty"] = honesty
        data["score"] = min(score, honesty) if honesty else score
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
    html_content = html_content.replace("{{FULL_NAME}}", escape_html(cfg.full_name))
    html_content = html_content.replace("{{CONTACT_LINE}}", contact_line_html(cfg))

    changes = []
    parsed = normalize_parsed(parsed, cfg)
    for tag in resume_tags(cfg):
        new_text = (parsed.get(tag) or "").strip()
        if not new_text:
            log.warning("No content for tag <%s> — leaving placeholder", tag)
            continue
        reasoning = parsed.get(f"R_{tag}") or "No specific reasoning provided."
        changes.append({"tag": tag, "new": new_text, "reasoning": reasoning})
        html_content = html_content.replace(f"{{{{{tag}}}}}", escape_html(new_text))

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
        lines.append(f"**Critique:** {eval_result.get('critique', '')}")
        gaps = eval_result.get("gaps") or []
        if gaps:
            lines.append("")
            lines.append("**Honest gaps:**")
            for gap in gaps:
                lines.append(f"- {gap}")
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


def pdf_scale(content_height_px: float, max_pages: int, page_h_px: float = LETTER_HEIGHT_PX) -> float:
    """Never shrink type to hit a multi-page target. Only shrink when pages == 1 and content overflows."""
    max_pages = max(1, int(max_pages))
    if max_pages > 1:
        return 1.0
    limit = page_h_px
    if content_height_px <= limit:
        return 1.0
    return min(1.0, (limit / content_height_px) * 0.99)


async def html_to_pdf(html_path: Path, pdf_path: Path, max_pages: int | None = None) -> None:
    from playwright.async_api import async_playwright

    cfg = load_config()
    max_pages = int(max_pages) if max_pages is not None else cfg.cv_pages
    max_pages = max(1, max_pages)
    file_url = "file://" + os.path.abspath(html_path)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(file_url, wait_until="networkidle")
        await page.emulate_media(media="print")
        await page.evaluate("() => document.fonts.ready")
        body_height = await page.evaluate("() => document.documentElement.scrollHeight")
        height = page_height_px(cfg)
        scale = pdf_scale(float(body_height), max_pages, height)
        page_budget = height * max_pages
        if body_height > page_budget and max_pages > 1:
            log.warning(
                "HTML height %.0fpx exceeds cv_format.pages=%s (%.0fpx). Not shrinking — trim a bullet.",
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


async def save_materials(
    company: str,
    role: str,
    llm_output: str,
    eval_result: dict | None = None,
    feedback_history: str = "",
    date_str: str | None = None,
    job: dict | None = None,
) -> Path:
    cfg = load_config()
    date_str = date_str or datetime.date.today().isoformat()
    folder_name = f"{slug(company)}-{slug(role)}-{date_str}"
    output_dir = cfg.applications_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = normalize_parsed(parse_tagged_output(llm_output))
    (output_dir / "llm_output_raw.txt").write_text(llm_output)

    html_out = output_dir / f"{cfg.cv_stem}.html"
    changes = apply_changes_to_html(parsed, html_out, cfg)
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

    meta = {
        "company": company,
        "role": role,
        "url": (job or {}).get("url") or "",
        "location": (job or {}).get("location") or "",
        "source": (job or {}).get("source") or (job or {}).get("channel") or "",
    }
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
    analysis_body.append(analysis)
    (output_dir / "analysis.md").write_text("\n".join(analysis_body).strip() + "\n")

    pdf_out = output_dir / f"{cfg.cv_stem}.pdf"
    try:
        await html_to_pdf(html_out, pdf_out)
        log.info("Compiled PDF: %s", pdf_out)
    except Exception as exc:
        log.error("PDF conversion failed: %s", exc)

    return output_dir
