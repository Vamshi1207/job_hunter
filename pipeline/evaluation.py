"""Structured fit evaluation: 1-5 global score, legitimacy, work-auth, evidence.

Complements the 0-100 ATS/honesty/voice gates with the portable core of
career-ops' (MIT) A-H evaluation contract:

- one holistic 1-5 fit score mapped from the ATS score (no fake precision;
  legitimacy and requirement-importance stay out of the number so history
  stays comparable for pattern analysis),
- a 4-tier work-authorization signal where only an explicit no-sponsorship
  JD is a hard stop,
- a posting-legitimacy checklist reported as observations, never verdicts,
- per-dimension evidence statuses + a Low/Medium/High evidence-confidence
  tier that never treats missing evidence as neutral,
- a ``## Machine Summary`` YAML fence so reports stay machine-parseable.

All helpers are deterministic (no LLM, no network) and safe to unit test.
"""

from __future__ import annotations

import re

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a hard dep of pipeline.jobs
    yaml = None  # type: ignore

# Score bands. The ethical apply line sits at 4.0: below it we recommend
# against applying unless the user names a reason.
APPLY_AT = 4.5
CONSIDER_AT = 4.0
RESEARCH_AT = 3.5

LEGIT_HIGH = "High Confidence"
LEGIT_CAUTION = "Proceed with Caution"
LEGIT_SUSPICIOUS = "Suspicious"

WORK_SPONSORS = "sponsors"
WORK_NOT_NEEDED = "not_needed"
WORK_UNSTATED = "unstated"
WORK_NO_SPONSORSHIP = "no_sponsorship"

CONF_LOW = "Low"
CONF_MEDIUM = "Medium"
CONF_HIGH = "High"

_EVIDENCE_STATES = ("supported", "partial", "unknown")
_EVIDENCE_DIMS = ("cv_match", "north_star", "compensation", "culture", "red_flags")

_BLOCKER_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"no sponsorship",
        r"without sponsorship",
        r"do n[o']t sponsor",
        r"does n[o']t sponsor",
        r"will n[o']t sponsor",
        r"cann[o']t sponsor",
        r"unable to sponsor",
        r"no visa sponsorship",
        r"must be authorized to work",
        r"must have (?:existing |current )?authorization to work",
        r"existing (?:work )?authorization required",
        r"citizenship required",
        r"(?:us|u\.s\.) citizens? only",
        r"only (?:us|u\.s\.|uk|canadian) citizens?",
        r"security clearance required",
        r"must (?:currently )?reside in",
    ]
]

_SPONSOR_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"will sponsor",
        r"sponsorship (?:available|provided|offered|considered)",
        r"visa sponsorship",
        r"relocation (?:assistance|support|package|offered)",
        r"open to (?:international|overseas|global) applicants",
        r"tn visa",
        r"h-?1b (?:sponsorship|transfer)",
    ]
]

_SCAM_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"pay (?:an? )?(?:application|training|equipment|refundable)[ -]?(?:fee|deposit)",
        r"wire transfer",
        r"send (?:us|money|crypto|bitcoin)",
        r"refundable deposit",
        r"purchase (?:the|your own) (?:equipment|starter kit)",
        r"no experience necessary.{0,80}earn \$",
    ]
]

_PAY_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\$\s?\d[\d,]*(?:\s?[-–—]\s?\$?\s?\d[\d,]*)?\s?(?:/|per\s)?(?:year|yr|hour|hr|month|annum|k\b)",
        r"£\s?\d",
        r"€\s?\d",
        r"\b\d{2,3}k\b.{0,20}(?:salary|compensation|base|ote)",
        r"\bsalary\b.{0,40}\$",
    ]
]

_REQUIREMENT_HEADING_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brequirements?\b",
        r"\bqualifications?\b",
        r"\bresponsibilities\b",
        r"\bwhat you.?ll (?:do|bring|need)\b",
        r"\bmust have\b",
        r"\bnice to have\b",
    ]
]


# ---------------------------------------------------------------------------
# 1-5 global score
# ---------------------------------------------------------------------------


def ats_to_global(ats_score: float | int | None) -> float:
    """Map a 0-100 ATS score onto the 1-5 fit scale.

    Linear on purpose: ATS 80 (our accept gate) lands at 4.2, i.e. just
    above the 4.0 worth-applying line; 62.5 lands on the 3.5 research line.
    """
    try:
        ats = float(ats_score or 0)
    except (TypeError, ValueError):
        ats = 0.0
    return round(max(1.0, min(5.0, 1.0 + ats * 0.04)), 1)


def decision_for_score(
    global_score: float | int | None,
    apply_at: float = APPLY_AT,
    consider_at: float = CONSIDER_AT,
    research_at: float = RESEARCH_AT,
) -> str:
    """Human decision band for a 1-5 score. Never invents a new band."""
    try:
        score = float(global_score or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= apply_at:
        return "Apply"
    if score >= consider_at:
        return "Consider"
    if score >= research_at:
        return "Research first"
    return "Skip"


def recommend_apply(global_score: float | int | None, consider_at: float = CONSIDER_AT) -> bool:
    """The ethical line: apply at 4.0+, otherwise only with a stated reason."""
    try:
        return float(global_score or 0) >= consider_at
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Work authorization (4-tier; only an explicit refusal blocks)
# ---------------------------------------------------------------------------


def _authorized_markets(cfg=None) -> set[str]:
    markets: set[str] = set()
    if cfg is None:
        return markets
    try:
        country = str(cfg.get("user.country") or "").strip().lower()
        if country:
            markets.add(country)
        home = str(cfg.get("hunt.home_country") or "").strip().lower()
        if home:
            markets.add(home)
        for key in ("career.target_markets", "hunt.search_locations", "hunt.extra_search_markets"):
            values = cfg.get(key) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                name = str(value or "").strip().lower()
                if name:
                    markets.add(name)
    except Exception:
        pass
    return markets


def _find_evidence(jd_text: str, patterns: list) -> str:
    text = jd_text or ""
    for rx in patterns:
        match = rx.search(text)
        if match:
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            return re.sub(r"\s+", " ", text[start:end]).strip()
    return ""


def _location_mentions_home(location: str, jd_text: str, markets: set[str]) -> bool:
    blob = f"{location or ''}\n{jd_text or ''}".lower()
    if not markets:
        return False
    return any(market and market in blob for market in markets)


def work_auth_signal(location: str = "", jd_text: str = "", cfg=None) -> dict:
    """Classify work authorization into sponsors/not_needed/unstated/no_sponsorship.

    Only ``no_sponsorship`` is a hard stop, and only when the JD explicitly
    refuses sponsorship for a role outside the user's authorized markets.
    """
    jd = jd_text or ""
    blocker_evidence = _find_evidence(jd, _BLOCKER_RES)
    sponsor_evidence = _find_evidence(jd, _SPONSOR_RES)
    markets = _authorized_markets(cfg)
    in_home = _location_mentions_home(location, jd, markets)

    if blocker_evidence and not in_home:
        return {
            "tier": WORK_NO_SPONSORSHIP,
            "hard_stop": True,
            "evidence": blocker_evidence,
        }
    if in_home:
        return {"tier": WORK_NOT_NEEDED, "hard_stop": False, "evidence": ""}
    if sponsor_evidence:
        return {"tier": WORK_SPONSORS, "hard_stop": False, "evidence": sponsor_evidence}
    return {"tier": WORK_UNSTATED, "hard_stop": False, "evidence": ""}


# ---------------------------------------------------------------------------
# Posting legitimacy (observations, not verdicts; never touches the score)
# ---------------------------------------------------------------------------


def assess_legitimacy(jd_text: str = "", url: str = "", location: str = "") -> dict:
    """Return {tier, observations}. Salary omission is neutral, never a signal."""
    jd = jd_text or ""
    observations: list[str] = []
    concerns = 0

    for rx in _SCAM_RES:
        if rx.search(jd):
            observations.append(f"Upfront-fee/deposit language found: '{rx.pattern}' — verify before engaging.")
            return {"tier": LEGIT_SUSPICIOUS, "observations": observations}

    length = len(jd.strip())
    if length < 400:
        concerns += 1
        observations.append(f"JD unusually short ({length} chars) — thin detail for a real opening.")
    elif not any(rx.search(jd) for rx in _REQUIREMENT_HEADING_RES):
        concerns += 1
        observations.append("No requirements/responsibilities section found — JD may be truncated or generic.")

    if url and not re.search(r"(greenhouse|lever|ashby|workday|linkedin\.com/jobs/view|indeed.*viewjob|myworkdayjobs|smartrecruiters|jobvite|icims|workable|bamboohr|ashbyhq)", url, re.IGNORECASE):
        concerns += 1
        observations.append("Apply URL is not a recognized ATS/job-board posting page — confirm it is a real opening.")
    if not url.strip():
        concerns += 1
        observations.append("No posting URL captured — liveness could not be verified.")

    if not location.strip() and length < 1200:
        concerns += 1
        observations.append("No location captured on a short JD — confirm work model before applying.")

    if concerns == 0:
        observations.append("Posting reads like a real, active opening (specific JD on a known channel).")
        tier = LEGIT_HIGH
    elif concerns == 1:
        tier = LEGIT_CAUTION
    else:
        tier = LEGIT_SUSPICIOUS
    return {"tier": tier, "observations": observations}


# ---------------------------------------------------------------------------
# Evidence confidence (missing evidence is flagged, never neutral)
# ---------------------------------------------------------------------------


def confidence_tier(
    statuses: dict | None = None,
    jd_accessible: bool = True,
    unresolved: list | None = None,
) -> str:
    """Apply the Low/Medium/High ordering rules to five evidence statuses."""
    statuses = dict(statuses or {})
    for dim in _EVIDENCE_DIMS:
        if statuses.get(dim) not in _EVIDENCE_STATES:
            statuses[dim] = "unknown"
    unknowns = sum(1 for dim in _EVIDENCE_DIMS if statuses[dim] == "unknown")
    partials = sum(1 for dim in _EVIDENCE_DIMS if statuses[dim] == "partial")
    material = list(unresolved or [])

    if (
        not jd_accessible
        or statuses.get("cv_match") == "unknown"
        or statuses.get("north_star") == "unknown"
        or unknowns >= 2
    ):
        return CONF_LOW
    if partials or unknowns or material:
        return CONF_MEDIUM
    return CONF_HIGH


def default_score_evidence(
    jd_text: str = "",
    title: str = "",
    target_roles: list | None = None,
) -> tuple[dict, list]:
    """Honest deterministic defaults: partial where text exists, unknown where
    only research or an LLM pass could answer. Callers upgrade statuses when
    they have real evidence."""
    jd = jd_text or ""
    if len(jd.strip()) < 50:
        return ({dim: "unknown" for dim in _EVIDENCE_DIMS}, ["JD inaccessible or too short to assess fit."])
    blob = f"{title or ''}\n{jd}".lower()
    targets = [str(t or "").lower() for t in (target_roles or []) if str(t or "").strip()]
    north_star: str = "supported" if targets and any(t and t in blob for t in targets) else "partial"
    compensation: str = "partial" if any(rx.search(jd) for rx in _PAY_RES) else "unknown"
    statuses = {
        "cv_match": "partial",
        "north_star": north_star,
        "compensation": compensation,
        "culture": "unknown",
        "red_flags": "partial",
    }
    gaps = []
    if compensation == "unknown":
        gaps.append("No pay figure in JD — compare against market quartile before deciding.")
    gaps.append("Culture signals unchecked — verify team norms before applying.")
    gaps.append("CV-match still needs the tailored-review pass to confirm.")
    return statuses, gaps[:3]


# ---------------------------------------------------------------------------
# Machine Summary fence
# ---------------------------------------------------------------------------


def render_machine_summary(
    company: str = "",
    role: str = "",
    ats_score: float | int | None = None,
    global_score: float | None = None,
    legitimacy: dict | None = None,
    work_auth: dict | None = None,
    hard_stops: list | None = None,
    soft_gaps: list | None = None,
    confidence: str = CONF_MEDIUM,
    score_evidence: dict | None = None,
    confidence_gaps: list | None = None,
    next_action: str = "",
    decision: str | None = None,
    recommend: bool | None = None,
) -> str:
    """Render the ``## Machine Summary`` YAML fence (exact keys, no prose)."""
    if global_score is None:
        global_score = ats_to_global(ats_score)
    decision = decision if decision is not None else decision_for_score(global_score)
    recommend = recommend if recommend is not None else recommend_apply(global_score)
    payload = {
        "company": company or "",
        "role": role or "",
        "score": global_score,
        "ats_score": int(ats_score or 0),
        "legitimacy_tier": (legitimacy or {}).get("tier", LEGIT_HIGH),
        "work_auth": (work_auth or {}).get("tier", WORK_UNSTATED),
        "final_decision": decision,
        "recommend_apply": recommend,
        "hard_stops": list(hard_stops or []),
        "soft_gaps": list(soft_gaps or []),
        "confidence": confidence,
        "score_evidence": dict(score_evidence or {}),
        "confidence_gaps": list(confidence_gaps or []),
        "next_action": next_action or "",
    }
    if yaml is not None:
        body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    else:  # pragma: no cover - fallback when PyYAML is unavailable
        lines = []
        for key, value in payload.items():
            lines.append(f"{key}: {value!r}")
        body = "\n".join(lines)
    return "## Machine Summary\n```yaml\n" + body + "\n```\n"


def parse_machine_summary(markdown: str = "") -> dict:
    """Parse the first ``## Machine Summary`` YAML fence. Returns {} if absent."""
    match = re.search(r"## Machine Summary\s*```ya?ml(.*?)```", markdown or "", re.S | re.IGNORECASE)
    if not match:
        return {}
    body = match.group(1).strip()
    if yaml is not None:
        try:
            data = yaml.safe_load(body)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    out: dict = {}
    score = re.search(r"^score:\s*([0-9.]+)", body, re.M)
    if score:
        try:
            out["score"] = float(score.group(1))
        except ValueError:
            pass
    decision = re.search(r"^final_decision:\s*[\"']?(.+?)[\"']?\s*$", body, re.M)
    if decision:
        out["final_decision"] = decision.group(1).strip()
    return out


def enrich_evaluation(eval_result: dict | None = None, job: dict | None = None, cfg=None) -> dict:
    """Return a copy of an ATS eval dict with 1-5 score, legitimacy,
    work-auth, evidence confidence and a rendered machine summary."""
    eval_result = dict(eval_result or {})
    job = job or {}
    jd_text = str(job.get("jd") or "")
    location = str(job.get("location") or "")
    company = str(job.get("company") or "")
    role = str(job.get("role") or "")

    ats_score = eval_result.get("score", 0)
    global_score = ats_to_global(ats_score)
    try:
        apply_at = float((cfg.get("evaluation.apply_at") if cfg else None) or APPLY_AT)
        consider_at = float((cfg.get("evaluation.consider_at") if cfg else None) or CONSIDER_AT)
        research_at = float((cfg.get("evaluation.research_at") if cfg else None) or RESEARCH_AT)
    except (TypeError, ValueError, AttributeError):
        apply_at, consider_at, research_at = APPLY_AT, CONSIDER_AT, RESEARCH_AT
    legitimacy = assess_legitimacy(jd_text, str(job.get("url") or ""), location)
    work_auth = work_auth_signal(location, jd_text, cfg)
    try:
        targets = list(cfg.get("career.target_roles") or []) if cfg else []
    except Exception:
        targets = []
    statuses, gaps = default_score_evidence(jd_text, f"{role}", targets)
    confidence = confidence_tier(statuses, jd_accessible=len(jd_text.strip()) >= 50)

    hard_stops: list = []
    if work_auth.get("hard_stop"):
        hard_stops = [f"Work authorization: {work_auth.get('evidence') or 'JD refuses sponsorship'}".strip()]
    soft_gaps = list(eval_result.get("gaps") or [])[:6]

    if recommend_apply(global_score, consider_at) and not work_auth.get("hard_stop"):
        next_action = f"Tailor CV and apply to {company or 'this role'} — score {global_score}/5."
    elif work_auth.get("hard_stop"):
        next_action = "Skip: work-authorization blocker. Override only with a stated reason."
    else:
        next_action = f"Skip or research first — score {global_score}/5 below the {consider_at} apply line."

    eval_result["global_score"] = global_score
    eval_result["fit_decision"] = decision_for_score(global_score, apply_at, consider_at, research_at)
    eval_result["recommend_apply"] = recommend_apply(global_score, consider_at)
    eval_result["legitimacy_tier"] = legitimacy["tier"]
    eval_result["legitimacy_observations"] = legitimacy["observations"]
    eval_result["work_auth"] = work_auth["tier"]
    eval_result["work_auth_evidence"] = work_auth.get("evidence", "")
    eval_result["work_auth_hard_stop"] = work_auth.get("hard_stop", False)
    eval_result["evidence_confidence"] = confidence
    eval_result["score_evidence"] = statuses
    eval_result["confidence_gaps"] = gaps
    eval_result["machine_summary"] = render_machine_summary(
        company=company,
        role=role,
        ats_score=ats_score,
        global_score=global_score,
        legitimacy=legitimacy,
        work_auth=work_auth,
        hard_stops=hard_stops,
        soft_gaps=soft_gaps,
        confidence=confidence,
        score_evidence=statuses,
        confidence_gaps=gaps,
        next_action=next_action,
        decision=decision_for_score(global_score, apply_at, consider_at, research_at),
        recommend=recommend_apply(global_score, consider_at),
    )
    return eval_result
