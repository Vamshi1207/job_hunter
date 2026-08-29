"""Paste-by-field application playbook. The user clicks Submit."""

from __future__ import annotations

from pathlib import Path

from pipeline.config import Config


def visa_answers(cfg: Config) -> dict:
    status = (cfg.get("visa.status") or "").lower()
    description = cfg.get("visa.description") or ""
    may_future = bool(cfg.get("visa.may_need_future_sponsorship", False))
    no_sponsor_now = status in {
        "citizen",
        "permanent-resident",
        "permanent_resident",
        "pr",
    } or "no sponsorship" in description.lower()
    return {
        "work_authorization": description or status,
        "sponsorship_now": "No" if no_sponsor_now else "Yes",
        "sponsorship_future": "Yes" if may_future else "No",
    }


def render_playbook(
    cfg: Config,
    job: dict,
    output_dir: Path,
    pdf_path: Path,
    cover_letter_path: Path,
    why_path: Path,
) -> str:
    visa = visa_answers(cfg)
    location = ", ".join(
        p for p in (cfg.get("user.city"), cfg.get("user.country")) if p
    )
    lines = [
        f"# Application playbook — {job['company']} / {job['role']}",
        "",
        "Fill the form yourself. **Do not** click Submit until you have reviewed every field.",
        "",
        f"- JD URL: {job.get('url') or '(none)'}",
        f"- Output folder: `{output_dir}`",
        "",
        "| Field | Paste |",
        "|---|---|",
        f"| First name | {cfg.preferred_name} |",
        f"| Last name | {cfg.last_name} |",
        f"| Full name | {cfg.full_name} |",
        f"| Email | {cfg.get('user.email', '')} |",
        f"| Phone | {cfg.get('user.phone', '')} |",
        f"| Location | {location} |",
        f"| LinkedIn | {cfg.get('user.linkedin', '')} |",
        f"| GitHub | {cfg.get('user.github', '')} |",
        f"| Resume / CV | `{pdf_path}` |",
        f"| Cover letter | `{cover_letter_path}` |",
        f"| Why this role / why you | `{why_path}` |",
        f"| Work authorisation | {visa['work_authorization']} |",
        f"| Need sponsorship now? | {visa['sponsorship_now']} |",
        f"| Need sponsorship in the future? | {visa['sponsorship_future']} |",
        f"| Where did you hear about us | {job['company']} careers page |",
        "",
        "Skip voluntary demographic questions unless you choose to answer them.",
        "",
    ]
    return "\n".join(lines)
