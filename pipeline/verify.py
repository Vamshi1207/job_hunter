"""Deterministic verification checkers (advisory only — never block a save).

Checkers, not judges: every function here is a pure string/set operation
with no LLM and no network. They catch what a critic can wave through:

- title drift: a job title quietly inflating past its canonical title,
- numeric grounding: every number in the tailored CV must trace to the
  source-of-truth text (cv_master + experience bank),
- ATS template lint: structural rules on the HTML template, loaded from
  ``resumes/ats-rules.yaml``.

Discipline (adapted from career-ops' verify-cv-facts / ats-lint, MIT):
a check that cannot run reports ``skipped`` — never a pass. An empty
source file fails loud, not open.
"""

from __future__ import annotations

import re

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a hard dep of pipeline.jobs
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Title drift
# ---------------------------------------------------------------------------

#: Seniority tokens. Warn when one appears in the tailored title but not in
#: the canonical title for the same employer.
SENIORITY_TOKENS = frozenset(
    {
        "intern", "junior", "jr", "mid", "senior", "sr",
        "staff", "principal", "lead", "manager", "director",
        "head", "vp", "vice", "president", "fellow", "distinguished",
        "i", "ii", "iii", "iv", "l3", "l4", "l5", "l6", "l7",
    }
)

_TITLE_STOPWORDS = frozenset({"of", "the", "a", "an", "and", "&", "-"})


def normalize_title(text: str = "") -> str:
    """Lowercase, punctuation-folded title for comparison."""
    text = str(text or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _significant_words(title: str) -> set[str]:
    return {word for word in normalize_title(title).split() if word not in _TITLE_STOPWORDS}


def check_title_drift(titles: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Compare tailored titles against canonical ones.

    Each entry: ``{prefix, employer, canonical, tailored}``. Returns
    ``(warnings, skipped)``. Plain reframes that share words ("Software
    Engineer" -> "Backend Engineer") are quiet; seniority escalation and
    total role swaps warn. Advisory: callers must not block on this.
    """
    warnings: list[dict] = []
    skipped: list[str] = []
    for entry in titles or []:
        prefix = str((entry or {}).get("prefix") or "?")
        employer = str((entry or {}).get("employer") or prefix)
        canonical = str((entry or {}).get("canonical") or "").strip()
        tailored = str((entry or {}).get("tailored") or "").strip()
        if not canonical:
            skipped.append(f"{employer}: no canonical title configured — drift not checked")
            continue
        if not tailored:
            skipped.append(f"{employer}: tailored title missing — drift not checked")
            continue
        canon_norm = normalize_title(canonical)
        tailored_norm = normalize_title(tailored)
        if not canon_norm or not tailored_norm:
            skipped.append(f"{employer}: title unparseable after normalization — drift not checked")
            continue
        if canon_norm == tailored_norm:
            continue
        canon_words = _significant_words(canonical)
        tailored_words = _significant_words(tailored)
        if canon_words and tailored_words and not (canon_words & tailored_words):
            warnings.append(
                {
                    "prefix": prefix,
                    "employer": employer,
                    "canonical": canonical,
                    "tailored": tailored,
                    "kind": "role_swap",
                    "detail": f"No shared words with '{canonical}' — confirm the role was not swapped",
                }
            )
            continue
        canon_tokens = set(canon_norm.split())
        tailored_tokens = set(tailored_norm.split())
        added_seniority = sorted((tailored_tokens - canon_tokens) & SENIORITY_TOKENS)
        if added_seniority:
            warnings.append(
                {
                    "prefix": prefix,
                    "employer": employer,
                    "canonical": canonical,
                    "tailored": tailored,
                    "kind": "seniority",
                    "detail": f"Adds seniority token(s) {', '.join(added_seniority)} absent from '{canonical}'",
                }
            )
            continue
    return warnings, skipped


# ---------------------------------------------------------------------------
# Numeric-claim grounding
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s*(?:k|K|m|M|million|billion)?\b")
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%(?!\w)|percent\b)", re.IGNORECASE)
_COUNTED_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*\+?\s*"
    r"(years?|yrs?|months?|people|engineers?|members?|users?|customers?|requests?|events?|"
    r"services?|pipelines?|models?|deployments?|incidents?|teams?|projects?|clients?|"
    r"transactions?|queries?|nodes?|pods?|regions?|ms|s\b|qps|tps)\b",
    re.IGNORECASE,
)
_SCALE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:[kKmMbB])\b")
_MULT_RE = re.compile(r"\b\d+(?:\.\d+)?x\b", re.IGNORECASE)

_CLAIM_RES = (_MONEY_RE, _PERCENT_RE, _COUNTED_RE, _SCALE_RE, _MULT_RE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.IGNORECASE,
)
_YEARS_EXP_RE = re.compile(r"\b\d+(?:\.\d+)?\s*\+?\s*(years?|yrs?)\b", re.IGNORECASE)

_MAGNITUDE_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
                          "million": 1_000_000, "billion": 1_000_000_000}
_MAGNITUDE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|m|b|million|billion)\b", re.IGNORECASE)


def _expand_magnitude(raw: str) -> list[str]:
    """Equivalent exact forms of a scaled number: '10k' -> ['10000'].

    Exactness-preserving (10k IS 10000), so money stays strict while
    format-tolerant ('$120k' matches a sourced '$120,000').
    """
    match = _MAGNITUDE_RE.search(raw or "")
    if not match:
        return []
    try:
        value = float(match.group(1)) * _MAGNITUDE_MULTIPLIERS[match.group(2).lower()]
    except (ValueError, KeyError):
        return []
    expanded = str(int(value)) if value.is_integer() else ("%f" % value).rstrip("0").rstrip(".")
    forms = [expanded]
    if "$" in raw:
        forms.insert(0, f"${expanded}")
    return forms

_CONTEXT_WINDOW = 70


def _normalize_numbers(text: str) -> str:
    text = str(text or "").lower().replace(",", "")
    text = text.replace("%", " percent ")
    return re.sub(r"\s+", " ", text).strip()


def _first_number(blob: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?", blob)
    return match.group(0) if match else ""


def extract_numeric_claims(resume_text: str = "") -> list[dict]:
    """Find numbers-with-units in tailored text. Dates, phones and versions
    are excluded; everything else needs grounding."""
    text = str(resume_text or "")
    candidates: list[tuple[int, int, str]] = []
    for rx in _CLAIM_RES:
        for match in rx.finditer(text):
            candidates.append((match.start(), match.end(), match.group(0)))
    # Longest match wins at each position; overlapping spans are one claim
    # ("$500k" from the money detector, not "$500k" + "500k").
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, raw in candidates:
        if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in kept):
            continue
        kept.append((start, end, raw))
    claims: list[dict] = []
    for start, end, raw in kept:
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 7:
            continue  # phone number, request id, or similar
        number = _first_number(raw)
        if len(number) == 4 and number.isdigit() and 1900 <= int(number) <= 2100:
            window = text[max(0, start - 20): end + 20]
            if _MONTH_RE.search(window) or re.search(r"[–—\-/]|to\s+\d", window):
                continue  # employment date, not a claim
        context_start = max(0, start - _CONTEXT_WINDOW)
        context_end = min(len(text), end + _CONTEXT_WINDOW)
        unit_after = ""
        lookahead = re.match(r"\s*\+?\s*([a-z]+)", text[end:end + 12], re.IGNORECASE)
        if lookahead:
            unit_after = lookahead.group(1).lower()
        claims.append(
            {
                "claim": re.sub(r"\s+", " ", raw).strip(),
                "number": number,
                "unit_after": unit_after,
                "context": re.sub(r"\s+", " ", text[context_start:context_end]).strip(),
            }
        )
    claims.sort(key=lambda item: text.find(item["claim"]))
    return claims


def ground_claims(
    claims: list[dict] | None = None,
    source_text: str = "",
    career_years: float | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Ground each claim against the source text.

    Returns ``(supported, reviews, unsupported)``:
    - supported: number+unit (or exact amount) found in sources,
    - review: number present but context unverified, or a plausible
      years-of-experience derivation — human must confirm,
    - unsupported: number absent from sources entirely.
    """
    claims = list(claims or [])
    if not claims:
        return [], [], []
    if not str(source_text or "").strip():
        return (
            [],
            [],
            [],
        )  # caller records the skip loudly via verification_skipped
    norm_source = _normalize_numbers(source_text)
    supported: list[dict] = []
    reviews: list[dict] = []
    unsupported: list[dict] = []
    for claim in claims:
        raw = str(claim.get("claim") or "")
        number = str(claim.get("number") or "")
        unit_after = str(claim.get("unit_after") or "")
        norm_claim = _normalize_numbers(raw)
        entry = {"claim": raw, "context": str(claim.get("context") or "")}
        if norm_claim and norm_claim in norm_source:
            supported.append({**entry, "status": "supported"})
            continue
        magnitude_forms = _expand_magnitude(raw)
        magnitude_hit = ""
        for form in magnitude_forms:
            if form.startswith("$"):
                if form in norm_source:
                    magnitude_hit = form
                    break
            elif re.search(rf"(?<![\d.]){re.escape(form)}(?![\d.])", norm_source):
                magnitude_hit = form
                break
        if magnitude_hit:
            # Exact magnitude, but the unit still needs a human glance
            # ("10k users" vs a sourced "10,000 requests" share digits only).
            unit = (unit_after or "").strip()
            if unit and re.search(
                rf"{re.escape(magnitude_hit)}[\s\w,]{{0,40}}\b{re.escape(unit)}\b"
                rf"|\b{re.escape(unit)}\b[\s\w,]{{0,40}}{re.escape(magnitude_hit)}",
                norm_source,
            ):
                supported.append({**entry, "status": "supported"})
                continue
            reviews.append(
                {
                    **entry,
                    "status": "review",
                    "reason": f"magnitude {magnitude_hit} present, unit unverified",
                }
            )
            continue
        if raw.strip().startswith("$"):
            unsupported.append({**entry, "status": "unsupported", "reason": "amount not found in sources"})
            continue
        years_match = _YEARS_EXP_RE.search(raw)
        if years_match and career_years is not None:
            try:
                claimed_years = float(_first_number(raw))
            except ValueError:
                claimed_years = None
            if claimed_years is not None and claimed_years <= float(career_years) + 2:
                reviews.append(
                    {
                        **entry,
                        "status": "review",
                        "reason": f"within stated experience ({career_years}y) — confirm derivation",
                    }
                )
                continue
        if number and re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", norm_source):
            reviews.append({**entry, "status": "review", "reason": "number present, context unverified"})
            continue
        unsupported.append({**entry, "status": "unsupported", "reason": "number absent from sources"})
    return supported, reviews, unsupported


# ---------------------------------------------------------------------------
# ATS template lint (rules live in resumes/ats-rules.yaml)
# ---------------------------------------------------------------------------


def _detect_no_layout_tables(html: str, rule: dict) -> list[str]:
    tables = re.findall(r"<table[\s>]", html or "", re.IGNORECASE)
    if tables:
        return [f"{len(tables)} <table> element(s) — tabular layout scrambles ATS reading order"]
    return []


def _detect_no_images(html: str, rule: dict) -> list[str]:
    images = re.findall(r"<img[\s>]", html or "", re.IGNORECASE)
    if images:
        return [f"{len(images)} <img> element(s) — images are invisible to ATS parsers"]
    return []


def _detect_contact_block(html: str, rule: dict) -> list[str]:
    blob = html or ""
    if "{{CONTACT_LINE}}" in blob:
        return []
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", blob):
        return []
    return ["no {{CONTACT_LINE}} placeholder and no email address — ATS needs contact text"]


def _detect_section_markers(html: str, rule: dict) -> list[str]:
    markers = rule.get("markers") or ["summary", "experience", "skills", "education"]
    missing = [name for name in markers if f"<!--cv-section:{name}-->" not in (html or "")]
    return [f"missing section marker(s): {', '.join(missing)}" for _ in [0] if missing]


def _detect_single_column(html: str, rule: dict) -> list[str]:
    # Row-internal micro-grids (bullet glyph + text, label + value, title +
    # date rail) preserve reading order and are fine. Flag genuine content
    # splits: two or more `fr` tracks, or a named two-column/sidebar
    # container. Pixel-measured sidebars need rendered widths to judge and
    # are out of scope (see the rule's `limited_because`).
    blob = html or ""
    if re.search(r"columns\s*:\s*[2-9]", blob):
        return ["CSS multi-column layout detected — keep one content column"]
    for match in re.finditer(r"([^{}]+)\{([^{}]*?grid-template-columns\s*:\s*([^;}{]+)[^}]*)\}", blob):
        selector, _, tracks = match.groups()
        fr_tracks = re.findall(r"\b\d*\.?\d*\s*fr\b", tracks)
        if len(fr_tracks) >= 2:
            name = re.sub(r"\s+", " ", selector).strip()[:60]
            return [f"CSS grid with multiple content tracks ({tracks.strip()}) on '{name}' — keep one content column"]
        if re.search(r"two[-_ ]?col|sidebar|left[-_ ]?col|right[-_ ]?col", selector, re.IGNORECASE):
            if len(re.findall(r"(?:\d+(?:fr|px|em|%)|auto|minmax\(|max-content|min-content)", tracks)) > 1:
                name = re.sub(r"\s+", " ", selector).strip()[:60]
                return [f"named two-column container '{name}' — keep one content column"]
    return []


DETECTORS = {
    "no_layout_tables": _detect_no_layout_tables,
    "no_images": _detect_no_images,
    "contact_block": _detect_contact_block,
    "section_markers": _detect_section_markers,
    "single_column": _detect_single_column,
}


def load_ats_rules(path=None) -> list[dict]:
    """Load lint rules. Returns [] when unreadable — callers record a skip."""
    if path is None or yaml is None:
        return []
    try:
        from pathlib import Path as _Path

        data = yaml.safe_load(_Path(path).read_text())
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("rules") or []
    return [rule for rule in (data or []) if isinstance(rule, dict) and rule.get("id")]


def lint_template(html_text: str = "", rules: list[dict] | None = None) -> dict:
    """Lint a CV template. Returns ``{findings, skipped}``.

    Rules without an implemented detector are reported under ``skipped``,
    never treated as passing.
    """
    findings: list[dict] = []
    skipped: list[str] = []
    for rule in rules or []:
        rule_id = str(rule.get("id") or "unnamed")
        detector_name = rule.get("detect")
        if not detector_name:
            skipped.append(f"{rule_id}: no detector ({rule.get('unimplemented_because') or 'not implemented'})")
            continue
        detector = DETECTORS.get(str(detector_name))
        if detector is None:
            skipped.append(f"{rule_id}: unknown detector '{detector_name}' — not checked")
            continue
        try:
            issues = detector(html_text, rule)
        except Exception as exc:
            skipped.append(f"{rule_id}: detector errored ({exc}) — not checked")
            continue
        for issue in issues:
            findings.append({"rule": rule_id, "severity": rule.get("severity", "warning"), "detail": issue})
    return {"findings": findings, "skipped": skipped}


# ---------------------------------------------------------------------------
# Orchestrator (advisory)
# ---------------------------------------------------------------------------


def verify_application(
    titles: list[dict] | None = None,
    resume_text: str = "",
    source_text: str = "",
    career_years: float | None = None,
) -> dict:
    """Run title-drift + numeric-grounding checkers. Pure advisory output::

        {
          "title_warnings": [...], "ungrounded_claims": [...],
          "claim_reviews": [...], "claims_supported": int,
          "verification_skipped": [...],
        }

    Never blocks; callers attach this to the eval payload for display.
    """
    title_warnings, title_skipped = check_title_drift(titles)
    claims = extract_numeric_claims(resume_text)
    skipped = list(title_skipped)
    if not str(source_text or "").strip():
        skipped.append("no source text (cv_master/bank unreadable) — numeric grounding skipped, not passed")
        supported, reviews, unsupported = [], [], []
    else:
        supported, reviews, unsupported = ground_claims(claims, source_text, career_years)
    if not claims and str(source_text or "").strip():
        skipped.append("no numeric claims found in tailored text — nothing to ground")
    return {
        "title_warnings": title_warnings,
        "ungrounded_claims": unsupported,
        "claim_reviews": reviews,
        "claims_supported": len(supported),
        "verification_skipped": skipped,
    }


    skipped = eval_result.get("verification_skipped") or []
    if skipped:
        lines.append("**Verification skipped (not passed):**")
        for item in skipped:
            lines.append(f"- {item}")
    return lines


# ---------------------------------------------------------------------------
# Skill-gap buckets (zero-LLM, pre-draft)
# ---------------------------------------------------------------------------

#: Headings that open the must-have section of a JD.
_REQUIREMENT_HEADING_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^(?:basic\s+|minimum\s+|key\s+)?requirements?\b",
        r"^(?:basic\s+|minimum\s+|key\s+)?qualifications?\b",
        r"^must[-\s]haves?\b",
        r"^required(\s+skills?)?\b",
        r"^what\s+you('ll|\s+will)?\s+bring\b",
        r"^you\s+have\b",
        r"^your\s+background\b",
        r"^we\s+require\b",
    ]
]

#: Headings that open the nice-to-have section.
_BONUS_HEADING_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^nice[-\s]to[-\s]haves?\b",
        r"^bonus(\s+points?)?\b",
        r"^preferred(\s+qualifications?|\s+skills?)?\b",
        r"^pluses?\b",
        r"^desired(\s+skills?)?\b",
        r"^good\s+to\s+have\b",
        r"^would\s+be\s+(great|nice|a\s+plus)\b",
        r"^optional\b",
    ]
]

#: Headings that end requirement scanning (duties, company pitch, logistics).
_SECTION_END_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^responsibilities\b",
        r"^what\s+you('ll|\s+will)?\s+do\b",
        r"^about\s+(the\s+)?(role|us|the\s+company|the\s+team)\b",
        r"^who\s+we\s+are\b",
        r"^benefits?\b",
        r"^compensation\b",
        r"^pay\b",
        r"^how\s+to\s+apply\b",
        r"^interview\s+process\b",
        r"^what\s+we\s+offer\b",
        r"^perks\b",
    ]
]

_FILLER_LEAD_RES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # Quantity prefixes: "5+ years of experience with X" -> "X".
        r"^\d+\+?\s*(years?|yrs?|months?)\s*(of\s+)?(experience\s*(with|in|of)?\s*)?",
        r"^(strong|deep|solid|proven|excellent|extensive|hands[-\s]?on|good|working|"
        r"practical|demonstrated|passion\s+for|familiarity\s+with|proficiency\s+(with|in|of)|"
        r"working\s+knowledge\s+of|knowledge\s+of|experience\s+(with|in|of)|"
        r"understanding\s+of|ability\s+to|background\s+in)\b\s*",
    ]
]

#: Trailing generic nouns that dilute skill phrases: "AWS deployment
#: experience" -> "AWS".
_TRAILING_GENERIC_RE = re.compile(
    r"\s+(background|experience|skills?|knowledge|expertise|development|deployment|work|history)$",
    re.IGNORECASE,
)

_GAP_STOPWORDS = frozenset(
    {
        "of", "the", "a", "an", "and", "or", "to", "in", "on", "for", "with",
        "you", "your", "our", "we", "will", "have", "has", "are", "is", "be",
        "as", "at", "by", "an", "etc", "including", "such", "well", "plus",
    }
)


def _clean_requirement_fragment(fragment: str) -> str:
    text = str(fragment or "").strip()
    text = re.sub(r"^[\-\*\u2022\u2023\u25e6\u25aa\u25ab>\d.)\]]+", "", text).strip()
    text = re.sub(r"\s*\(.*?\)\s*", " ", text).strip()
    for _ in range(3):
        reduced = text
        for rx in _FILLER_LEAD_RES:
            reduced = rx.sub("", reduced).strip()
        reduced = _TRAILING_GENERIC_RE.sub("", reduced).strip()
        if reduced == text:
            break
        text = reduced
    return re.sub(r"\s+", " ", text).strip(" ,;:")


def _phrase_tokens(phrase: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#]+", phrase.lower())
    return [word for word in words if len(word) >= 2 and word not in _GAP_STOPWORDS]


def _token_present(token: str, alpha_source: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", alpha_source) is not None


def check_skill_gaps(jd_text: str = "", source_text: str = "") -> dict:
    """Bucket JD requirements against the source text (no LLM).

    Returns ``{existing, supported, gaps, inconclusive, reason}`` where each
    bucket holds ``{phrase, importance}`` items (importance: required|bonus).
    ``existing`` = fully evidenced, ``supported`` = partially evidenced,
    ``gaps`` = no trace in sources. An inconclusive result (no requirements
    section, no candidates, empty JD, or no sources) says so explicitly and
    must never be read as "no gaps".
    """
    jd = str(jd_text or "").strip()
    if not jd:
        return {"existing": [], "supported": [], "gaps": [],
                "inconclusive": True, "reason": "empty JD — nothing was scanned"}
    if not str(source_text or "").strip():
        return {"existing": [], "supported": [], "gaps": [],
                "inconclusive": True, "reason": "no source text — nothing was classified"}
    alpha_source = re.sub(r"[^a-z0-9]+", " ", str(source_text).lower())

    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None

    def heading_kind(line: str) -> str | None:
        clean = re.sub(r"^#+\s*", "", line.strip()).strip().strip("*_:").strip()
        if len(clean) > 70:
            return None
        if any(rx.search(clean) for rx in _REQUIREMENT_HEADING_RES):
            return "required"
        if any(rx.search(clean) for rx in _BONUS_HEADING_RES):
            return "bonus"
        if any(rx.search(clean) for rx in _SECTION_END_RES):
            return "other"
        return None

    for raw_line in jd.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        kind = heading_kind(line)
        if kind is not None:
            current = (kind, [])
            sections.append(current)
            continue
        if current is None:
            current = ("other", [])
            sections.append(current)
        current[1].append(line)

    buckets: dict[str, list[dict]] = {"existing": [], "supported": [], "gaps": []}
    seen: set[str] = set()
    scanned_required = any(kind == "required" for kind, _ in sections)
    scanned_bonus = any(kind == "bonus" for kind, _ in sections)
    candidate_count = 0

    for kind, lines in sections:
        if kind == "other":
            continue
        for line in lines:
            fragments = re.split(r"[;]", line)
            if len(fragments) == 1:
                fragments = re.split(r",(?![^(]*\))", line) if "," in line else [line]
            split_fragments: list[str] = []
            for fragment in fragments:
                split_fragments.extend(
                    re.split(r"\s+(?:and|or)\s+|\s+/\s+", fragment)
                )
            for fragment in split_fragments:
                phrase = _clean_requirement_fragment(fragment)
                if len(phrase) < 3 or not re.search(r"[a-z]{2,}", phrase, re.IGNORECASE):
                    continue
                if len(phrase.split()) > 6:
                    continue  # duties prose, not a skill
                key = phrase.lower()
                if key in seen:
                    continue
                seen.add(key)
                tokens = _phrase_tokens(phrase)
                if not tokens:
                    continue
                candidate_count += 1
                present = sum(1 for token in tokens if _token_present(token, alpha_source))
                item = {"phrase": phrase, "importance": kind}
                if present == len(tokens):
                    buckets["existing"].append(item)
                elif present >= max(1, len(tokens) / 2):
                    buckets["supported"].append(item)
                else:
                    buckets["gaps"].append(item)

    if not scanned_required and not scanned_bonus:
        return {"existing": [], "supported": [], "gaps": [],
                "inconclusive": True, "reason": "no requirements section recognized"}
    if candidate_count == 0:
        return {"existing": [], "supported": [], "gaps": [],
                "inconclusive": True, "reason": "requirements section scanned but no skill candidates extracted"}
    for key in buckets:
        required = [item for item in buckets[key] if item["importance"] == "required"]
        bonus = [item for item in buckets[key] if item["importance"] == "bonus"][:6]
        buckets[key] = required + bonus
    return {**buckets, "inconclusive": False, "reason": ""}


def skill_gap_note_lines(jd_brief: dict | None = None) -> list[str]:
    """Disclosure lines for analysis output. Inconclusive is stated, never hidden."""
    gaps_info = (jd_brief or {}).get("skill_gaps") or {}
    if not gaps_info:
        return []
    if gaps_info.get("inconclusive"):
        return [f"**Skill-gap check:** inconclusive — {gaps_info.get('reason') or 'no result'}."]
    lines: list[str] = []
    required_gaps = [
        item.get("phrase") if isinstance(item, dict) else str(item)
        for item in gaps_info.get("gaps") or []
        if (item.get("importance") if isinstance(item, dict) else "required") == "required"
    ]
    if required_gaps:
        lines.append(f"**Known gaps (proceeding anyway):** {', '.join(required_gaps)}.")
    return lines


def verification_note_lines(eval_result: dict | None = None) -> list[str]:
    """Render advisory verification findings as markdown lines (no header)."""
    eval_result = eval_result or {}
    lines: list[str] = []
    warnings = eval_result.get("title_warnings") or []
    if warnings:
        lines.append("**Title drift warnings (advisory — titles were not changed):**")
        for item in warnings:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('employer')}: '{item.get('tailored')}' vs "
                    f"canonical '{item.get('canonical')}' — {item.get('detail')}"
                )
            else:
                lines.append(f"- {item}")
    ungrounded = eval_result.get("ungrounded_claims") or []
    if ungrounded:
        lines.append("**Ungrounded numbers (advisory — confirm against cv_master/bank):**")
        for item in ungrounded:
            if isinstance(item, dict):
                lines.append(f"- {item.get('claim')} — {item.get('reason')}")
            else:
                lines.append(f"- {item}")
    reviews = eval_result.get("claim_reviews") or []
    if reviews:
        lines.append("**Numbers needing review:**")
        for item in reviews:
            if isinstance(item, dict):
                lines.append(f"- {item.get('claim')} — {item.get('reason')}")
            else:
                lines.append(f"- {item}")
    skipped = eval_result.get("verification_skipped") or []
    if skipped:
        lines.append("**Verification skipped (not passed):**")
        for item in skipped:
            lines.append(f"- {item}")
    return lines
