"""Dead-posting gate: skip LLM tailor work when a listing is already closed.

Runs *before* any LLM call so expired postings never cost tokens or
tracker rows. Deterministic string/HTTP checks only — no network, no LLM.

Inspired by career-ops (MIT) `liveness-core.mjs` hard-expired patterns and
the liveness-before-eval rule. Implemented independently for this repo:
patterns are a curated subset, matching is normalized the same way
(ASCII quotes, accents stripped, whitespace collapsed).
"""

from __future__ import annotations

import re
import unicodedata

#: classify_liveness() outcomes.
ACTIVE = "active"
EXPIRED = "expired"
UNCERTAIN = "uncertain"

_HARD = [
    r"job (is )?no longer available",
    r"job.*no longer open",
    # "... has been filled" about the role itself, not "fill out the form".
    r"\b(?:job|jobs|position|role|posting|opening|vacancy|vacancies|requisition|req|listing)\b[\s\S]{0,60}has been filled\b(?!\s+out)",
    r"this job has expired",
    r"job posting has expired",
    r"this job posting (has )?expired",
    r"no longer accepting applications",
    r"this (?:position|role|job) (?:is )?no longer",
    # Hyphen guard: "closed-loop control" prose must not match.
    r"this (?:job|role|position)(?: listing)? is closed\b(?!-)",
    r"job (?:listing )?not found",
    r"can't view this job because it's not available",
    r"the page you are looking for doesn\.t exist",
    r"page not found",
    r"applications?\s+(?:(?:have|are|is)\s+)?closed",
    r"position (?:has been )?filled",
    r"role (?:has been )?filled",
    r"vacancy (?:has been )?filled",
    # French closure banners.
    r"offre (?:expiree|n'est plus disponible)",
    r"(?:cette )?offre n'est plus (?:disponible|en ligne|active)",
    r"(?:offre|poste|annonce) (?:deja )?pourvu(?:e)?",
    r"offre (?:cloturee|desactivee|terminee)",
    r"ce poste n'est plus (?:disponible|a pourvoir|ouvert)",
    r"recrutement (?:termine|cloture)",
    r"candidatures (?:closes|cloturees)",
    # German / Dutch / Spanish / Polish closures.
    r"diese stelle (?:ist )?(?:nicht mehr|bereits) besetzt",
    r"deze vacature is niet meer gepubliceerd",
    r"\boferta cerrada el \d{1,2}/\d{1,2}/\d{4}",
    r"ogloszenie wygaslo",
    r"oferta (?:jest )?nieaktualna",
    r"oferta archiwalna",
]

#: Broad phrases that appear on both dead pages and live pages
#: (similar-jobs carousels, FAQ, filter chips). Never expire on these
#: alone — they only downgrade active -> uncertain.
_WEAK = [
    r"\bjob expired\b",
    r"\bposition closed\b",
    r"\bvacancy closed\b",
    r"\brequisition closed\b",
    r"\bapplications close\b",
]

#: A search-results page is not a posting — flag as uncertain.
_LISTING_PAGE = [
    r"\d+\s+jobs?\s+found",
    r"search for jobs page is loaded",
]

HARD_EXPIRED_RES = [re.compile(p, re.IGNORECASE) for p in _HARD]
WEAK_EXPIRED_RES = [re.compile(p, re.IGNORECASE) for p in _WEAK]
LISTING_PAGE_RES = [re.compile(p, re.IGNORECASE) for p in _LISTING_PAGE]


def normalize_for_match(text: str = "") -> str:
    """Fold quotes/accents/whitespace so one spelling matches all banners."""
    if not isinstance(text, str):
        return ""
    text = (
        text.replace("‘", "'")
        .replace("’", "'")
        .replace("ʼ", "'")
        .replace("′", "'")
        .replace("´", "'")
        .replace("`", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    # ł does not decompose under NFD — fold it explicitly (pracuj.pl banners).
    text = text.replace("ł", "l").replace("Ł", "L")
    return re.sub(r"\s+", " ", text).strip()


def classify_liveness(
    jd_text: str = "",
    page_text: str = "",
    status_code: int | None = None,
    has_apply: bool | None = None,
) -> str:
    """Return active | expired | uncertain for a posting.

    - HTTP 404/410 is expired; 429/5xx is uncertain (retryable).
    - A hard closure banner is expired even with an Apply button present.
    - Weak signals only downgrade to uncertain, never to expired.
    - Empty content with no Apply control is uncertain, never active.
    """
    if status_code in (404, 410):
        return EXPIRED
    if status_code == 429 or (isinstance(status_code, int) and 500 <= status_code <= 599):
        return UNCERTAIN

    blob = normalize_for_match(f"{jd_text or ''}\n{page_text or ''}")
    if not blob:
        return UNCERTAIN if has_apply is not True else UNCERTAIN

    for rx in HARD_EXPIRED_RES:
        if rx.search(blob):
            return EXPIRED
    for rx in LISTING_PAGE_RES:
        if rx.search(blob):
            return UNCERTAIN
    for rx in WEAK_EXPIRED_RES:
        if rx.search(blob):
            return UNCERTAIN
    if len(blob) < 50 and has_apply is not True:
        return UNCERTAIN
    return ACTIVE


def is_expired(
    jd_text: str = "",
    page_text: str = "",
    status_code: int | None = None,
    has_apply: bool | None = None,
) -> bool:
    """True only on a confident expired signal. Uncertain means proceed."""
    return (
        classify_liveness(jd_text, page_text, status_code, has_apply) == EXPIRED
    )
