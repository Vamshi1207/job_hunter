"""Follow-up cadence + funnel stats for applications.

Turns the tracker into an action list: which applied roles are overdue for
a nudge, and how the funnel converts (draft -> applied -> interview ->
offer). Deterministic date math only — no LLM, no network.

Inspired by career-ops (MIT) `followup-cadence.mjs` cadence defaults and
its funnel/pattern scripts. Implemented independently for this repo's
tracker layout (``applications/_tracker.md`` + per-package ``job.json``).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

#: Days between nudges. Same shape as the upstream defaults.
DEFAULT_CADENCE = {
    "applied_first": 7,
    "applied_subsequent": 7,
    "applied_max_followups": 2,
    "responded_initial": 1,
    "responded_subsequent": 3,
    "interview_thankyou": 1,
}

#: Config keys (``followup:`` section) mapped onto cadence keys.
PROFILE_CADENCE_KEYS = {
    "applied_first_days": "applied_first",
    "applied_subsequent_days": "applied_subsequent",
    "applied_max_followups": "applied_max_followups",
    "responded_initial_days": "responded_initial",
    "responded_subsequent_days": "responded_subsequent",
    "interview_thankyou_days": "interview_thankyou",
}

STAGE_DRAFT = "draft"
STAGE_APPLIED = "applied"
STAGE_INTERVIEW = "interview"
STAGE_OFFER = "offer"
STAGE_REJECTED = "rejected"
STAGE_HIRED = "hired"

_ACTIVE_STAGES = (STAGE_APPLIED, STAGE_INTERVIEW, STAGE_OFFER)


def fold_status(status: str = "") -> str:
    """Fold a free-form tracker status into a canonical funnel stage."""
    text = str(status or "").lower()
    if any(key in text for key in ("hired", "accepted", "signed")):
        return STAGE_HIRED
    # Terminal states win over the stage that produced them
    # ("rejected after onsite" is a rejection, not an interview).
    if any(key in text for key in ("reject", "declined", "ghost", "closed", "no offer", "not moving")):
        return STAGE_REJECTED
    if any(key in text for key in ("offer",)):
        return STAGE_OFFER
    if any(key in text for key in ("interview", "screen", "onsite", "loop", "assessment")):
        return STAGE_INTERVIEW
    if any(key in text for key in ("appl", "submit")):
        return STAGE_APPLIED
    return STAGE_DRAFT


def resolve_cadence(cfg=None, overrides: dict | None = None) -> dict:
    """Merge DEFAULT_CADENCE with the ``followup:`` config section + overrides."""
    cadence = dict(DEFAULT_CADENCE)
    section: dict = {}
    if cfg is not None:
        try:
            section = dict(cfg.get("followup") or {})
        except Exception:
            section = {}
    for profile_key, cadence_key in PROFILE_CADENCE_KEYS.items():
        try:
            raw = section.get(profile_key)
        except AttributeError:
            raw = None
        if isinstance(raw, bool):
            continue
        try:
            value = int(str(raw).strip().lstrip("+"))
        except (TypeError, ValueError, AttributeError):
            continue
        if value >= 0:
            cadence[cadence_key] = value
    for key, raw in dict(overrides or {}).items():
        if key in cadence:
            try:
                cadence[key] = int(raw)
            except (TypeError, ValueError):
                continue
    return cadence


def followups_enabled(cfg=None) -> bool:
    """``followup.enabled: false`` turns follow-up surfacing off. Default on."""
    if cfg is None:
        return True
    try:
        section = cfg.get("followup")
    except Exception:
        return True
    if not isinstance(section, dict):
        return True
    return section.get("enabled", True) is not False


def _parse_day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _days_between(today: date, day: date | None) -> int | None:
    if day is None:
        return None
    return (today - day).days


def due_followups(
    apps: list[dict] | None = None,
    today: date | str | None = None,
    cadence: dict | None = None,
) -> list[dict]:
    """Return overdue follow-up actions, most overdue first.

    Each input is a dict with ``company/role/status`` plus ``date`` (or
    ``applied_at``) as the stage-entry date, ``followups`` (int, sent so far)
    and ``last_followup`` (date of the last nudge). Tracker rows without an
    explicit count default to 0 nudges sent.
    """
    if isinstance(today, str):
        today = _parse_day(today) or date.today()
    today = today or date.today()
    cad = dict(DEFAULT_CADENCE)
    cad.update(cadence or {})

    due: list[dict] = []
    for app in apps or []:
        stage = fold_status(app.get("status"))
        if stage not in _ACTIVE_STAGES:
            continue
        if stage == STAGE_OFFER:
            continue  # an offer needs a decision, not a nudge
        try:
            sent = int(app.get("followups") or 0)
        except (TypeError, ValueError):
            sent = 0
        anchor = _parse_day(app.get("last_followup")) or _parse_day(app.get("applied_at")) or _parse_day(app.get("date"))
        if anchor is None:
            continue
        age = _days_between(today, anchor)
        if age is None or age < 0:
            continue

        if stage == STAGE_APPLIED:
            if sent >= cad["applied_max_followups"]:
                continue
            wait = cad["applied_first"] if sent == 0 else cad["applied_subsequent"]
            if age >= wait:
                due.append(
                    {
                        "company": app.get("company") or "",
                        "role": app.get("role") or "",
                        "stage": stage,
                        "days_waiting": age,
                        "overdue_by": age - wait,
                        "followup_number": sent + 1,
                        "action": f"Follow-up #{sent + 1}: short check-in on {app.get('company') or 'the role'} ({age}d since last touch).",
                    }
                )
        elif stage == STAGE_INTERVIEW:
            wait = cad["interview_thankyou"] if sent == 0 else cad["responded_subsequent"]
            if age >= wait:
                kind = "thank-you" if sent == 0 else f"check-in #{sent + 1}"
                due.append(
                    {
                        "company": app.get("company") or "",
                        "role": app.get("role") or "",
                        "stage": stage,
                        "days_waiting": age,
                        "overdue_by": age - wait,
                        "followup_number": sent + 1,
                        "action": f"Interview {kind} for {app.get('company') or 'the role'} ({age}d).",
                    }
                )
    due.sort(key=lambda item: item["overdue_by"], reverse=True)
    return due


def funnel_stats(apps: list[dict] | None = None) -> dict:
    """Count stages + conversion rates + per-channel advance rates."""
    stages: dict[str, int] = {stage: 0 for stage in (STAGE_DRAFT, STAGE_APPLIED, STAGE_INTERVIEW, STAGE_OFFER, STAGE_REJECTED, STAGE_HIRED)}
    channels: dict[str, dict[str, int]] = {}
    for app in apps or []:
        stage = fold_status(app.get("status"))
        stages[stage] = stages.get(stage, 0) + 1
        channel = str(app.get("channel") or app.get("source") or "unknown").strip() or "unknown"
        bucket = channels.setdefault(channel, {"packages": 0, "applied": 0, "interviewed": 0})
        bucket["packages"] += 1
        if stage in (STAGE_APPLIED, STAGE_INTERVIEW, STAGE_OFFER, STAGE_HIRED):
            bucket["applied"] += 1
        if stage in (STAGE_INTERVIEW, STAGE_OFFER, STAGE_HIRED):
            bucket["interviewed"] += 1

    total = sum(stages.values())
    applied = stages[STAGE_APPLIED] + stages[STAGE_INTERVIEW] + stages[STAGE_OFFER] + stages[STAGE_HIRED] + 0
    # Rejected rows were applied too (a rejection is a response to an application).
    responded = applied + stages[STAGE_REJECTED]
    interviewed = stages[STAGE_INTERVIEW] + stages[STAGE_OFFER] + stages[STAGE_HIRED]
    offers = stages[STAGE_OFFER] + stages[STAGE_HIRED]

    def rate(part: int, whole: int) -> float | None:
        return round(part / whole, 3) if whole else None

    for bucket in channels.values():
        bucket["apply_rate"] = rate(bucket["applied"], bucket["packages"])
        bucket["interview_rate"] = rate(bucket["interviewed"], bucket["applied"])

    return {
        "total": total,
        "stages": stages,
        "applied": responded,
        "applied_rate": rate(responded, total),
        "interview_rate": rate(interviewed, responded),
        "offer_rate": rate(offers, interviewed),
        "channels": channels,
    }


def parse_tracker_rows(markdown: str = "") -> list[dict]:
    """Parse an ``applications/_tracker.md`` table into app dicts."""
    apps: list[dict] = []
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 5 or cells[0].lower() == "date" or set(cells[0]) <= {"-", " "}:
            continue
        follow_cell = cells[6] if len(cells) > 6 else ""
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", follow_cell)
        apps.append(
            {
                "date": cells[0],
                "company": cells[1] if len(cells) > 1 else "",
                "role": cells[2] if len(cells) > 2 else "",
                "channel": cells[3] if len(cells) > 3 else "",
                "status": cells[4] if len(cells) > 4 else "",
                "folder": cells[5] if len(cells) > 5 else "",
                "followups": len(dates),
                "last_followup": dates[-1] if dates else "",
            }
        )
    return apps


def load_applications(cfg=None) -> list[dict]:
    """Merge tracker rows with per-package job.json applied flags."""
    from pipeline.reports import list_packages

    if cfg is None:
        from pipeline.config import load_config

        cfg = load_config()
    try:
        tracker_text = cfg.tracker_path.read_text() if cfg.tracker_path.exists() else ""
    except OSError:
        tracker_text = ""
    apps = parse_tracker_rows(tracker_text)
    try:
        for pkg in list_packages(cfg):
            if pkg.get("applied") and not any(
                (row.get("folder") or "").endswith(pkg.get("id") or "~~")
                or (
                    (row.get("company") or "").lower() == (pkg.get("company") or "").lower()
                    and (row.get("role") or "").lower() == (pkg.get("role") or "").lower()
                )
                for row in apps
            ):
                apps.append(
                    {
                        "date": pkg.get("applied_at") or pkg.get("date") or "",
                        "company": pkg.get("company") or "",
                        "role": pkg.get("role") or "",
                        "channel": pkg.get("source") or "",
                        "status": "applied",
                        "folder": pkg.get("id") or "",
                        "followups": 0,
                        "last_followup": "",
                    }
                )
    except Exception:
        pass
    return apps


def summary_text(apps: list[dict] | None = None, today: date | str | None = None, cadence: dict | None = None) -> str:
    """Human-readable dashboard: funnel counts + overdue follow-ups."""
    stats = funnel_stats(apps)
    due = due_followups(apps, today=today, cadence=cadence)
    lines = ["# Follow-ups & funnel", ""]
    lines.append(
        f"Packages: {stats['total']} · applied: {stats['applied']} "
        f"({stats['applied_rate'] if stats['applied_rate'] is not None else 'n/a'}) · "
        f"interview rate: {stats['interview_rate'] if stats['interview_rate'] is not None else 'n/a'} · "
        f"offer rate: {stats['offer_rate'] if stats['offer_rate'] is not None else 'n/a'}"
    )
    lines.append("")
    if not due:
        lines.append("No overdue follow-ups. 🎉")
    else:
        lines.append(f"Overdue: {len(due)}")
        for item in due:
            lines.append(f"- {item['company']} / {item['role']} — {item['action']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python3 -m pipeline.followup [--summary] [--overdue-only]``."""
    import argparse

    parser = argparse.ArgumentParser(description="Follow-up cadence + funnel stats. Read-only; never sends anything.")
    parser.add_argument("--summary", action="store_true", help="Human-readable dashboard instead of JSON.")
    parser.add_argument("--overdue-only", action="store_true", help="With --summary, only list overdue items.")
    args = parser.parse_args(argv)

    from pipeline.config import load_config

    cfg = load_config()
    if not followups_enabled(cfg):
        if args.summary:
            print("Follow-ups disabled (followup.enabled: false).")
        else:
            print(json.dumps({"disabled": True, "due": [], "stats": funnel_stats([])}))
        return 0
    apps = load_applications(cfg)
    cadence = resolve_cadence(cfg)
    stats = funnel_stats(apps)
    due = due_followups(apps, today=date.today(), cadence=cadence)
    if args.summary:
        if args.overdue_only:
            if not due:
                print("No overdue follow-ups. 🎉")
            else:
                for item in due:
                    print(f"- {item['company']} / {item['role']} — {item['action']}")
        else:
            print(summary_text(apps, today=date.today(), cadence=cadence), end="")
    else:
        print(json.dumps({"due": due, "stats": stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
