"""Geography dataset for the hunt scope gates.

All place knowledge (country names, aliases, provinces, states, cities,
region words) lives in geo/regions.yaml — never hardcoded here. Per-user
additions belong in config.yaml (hunt.home_aliases, hunt.eligible_phrases).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

_CACHE: dict[str, dict] = {}


def default_path() -> Path:
    """geo/regions.yaml under the workspace root, else the bundled copy."""
    env = os.environ.get("JOB_SEARCH_ROOT")
    if env:
        cand = Path(env) / "geo" / "regions.yaml"
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "geo" / "regions.yaml"


def load_regions(path: Path | str | None = None) -> dict:
    """Parse the regions file (cached per path). Returns {} when missing."""
    resolved = str(path or default_path())
    if resolved in _CACHE:
        return _CACHE[resolved]
    data: dict = {}
    try:
        text = Path(resolved).read_text()
        parsed = yaml.safe_load(text) or {}
        if isinstance(parsed, dict):
            data = parsed
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("countries"), dict):
        data["countries"] = {}
    if not isinstance(data.get("region_words"), list):
        data["region_words"] = []
    _CACHE[resolved] = data
    return data


def get_regions(cfg=None) -> dict:
    """Regions for a config (workspace.geo override) or the bundled dataset."""
    if cfg is not None:
        try:
            override = cfg.path("workspace.geo", "geo/regions.yaml")
            if override.is_file():
                return load_regions(override)
        except (AttributeError, TypeError, ValueError):
            pass
    return load_regions(default_path())


def countries(regions: dict) -> dict:
    raw = (regions or {}).get("countries")
    return raw if isinstance(raw, dict) else {}


def display_name(regions: dict, key: str) -> str:
    entry = countries(regions).get(key or "", {})
    if isinstance(entry, dict) and entry.get("display"):
        return str(entry["display"])
    return str(key or "")


def _str_list(entry: dict, field: str) -> list[str]:
    raw = entry.get(field) if isinstance(entry, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in (raw or []) if str(item).strip()]


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.\s]+", " ", (text or "").lower())).strip()


def normalize_place(name: str, regions: dict) -> str:
    """Canonical country key for a configured market name ('USA' -> 'united states').

    The alias map is built from the dataset, not hardcoded. Unknown names
    come back normalized but unchanged.
    """
    key = _norm_key(name)
    if not key:
        return ""
    for country_key, entry in countries(regions).items():
        if not isinstance(entry, dict):
            continue
        candidates = [str(country_key)] + _str_list(entry, "aliases")
        for candidate in candidates:
            if _norm_key(candidate) == key:
                return str(country_key)
    return key


def _matchable(tokens: list[str]) -> list[str]:
    """Tokens safe for text matching. Short ones (us, ca, in, uk-as-text) are
    lookup-only: matching them would false-positive on ordinary words."""
    out = []
    for token in tokens:
        text = str(token or "").strip()
        if len(text) > 2:
            out.append(text)
    return out


def match_names(regions: dict, key: str) -> list[str]:
    """Names identifying a country in text: key display-ish name, aliases, places."""
    entry = countries(regions).get(key or {}, {})
    if not isinstance(entry, dict):
        entry = {}
    names = [str(key)] if key else []
    names.extend(_str_list(entry, "aliases"))
    names.extend(_str_list(entry, "places"))
    names.extend(_str_list(entry, "default_home_aliases"))
    return _matchable(names)


def auth_names(regions: dict, key: str) -> list[str]:
    """match_names plus abbreviations that are unambiguous inside restrictive
    phrasing ('US citizenship required', 'open to US applicants')."""
    entry = countries(regions).get(key or {}, {})
    extra = _str_list(entry, "auth_abbrs") if isinstance(entry, dict) else []
    seen: set[str] = set()
    out: list[str] = []
    for name in match_names(regions, key) + extra:
        low = name.lower()
        if low not in seen:
            seen.add(low)
            out.append(name)
    return out


def auth_abbrs(regions: dict, key: str) -> list[str]:
    entry = countries(regions).get(key or {}, {})
    return _str_list(entry, "auth_abbrs") if isinstance(entry, dict) else []


def all_match_tokens(regions: dict) -> list[str]:
    """Every matchable place token across countries, plus region words."""
    out: list[str] = []
    for key in countries(regions):
        out.extend(match_names(regions, key))
    out.extend(_str_list({"region_words": (regions or {}).get("region_words")}, "region_words"))
    return out


def codes(regions: dict, key: str) -> list[str]:
    entry = countries(regions).get(key or {}, {})
    return _str_list(entry, "codes") if isinstance(entry, dict) else []


def codes_pattern(regions: dict, key: str | None = None) -> re.Pattern | None:
    """`, XX` province/state-code matcher for one country (or all when key is None)."""
    if key:
        items = codes(regions, key)
    else:
        items = []
        for country_key in countries(regions):
            items.extend(codes(regions, country_key))
    items = sorted({item.lower() for item in items if item}, key=len, reverse=True)
    if not items:
        return None
    return re.compile(r",\s*(?:%s)\b" % "|".join(re.escape(item) for item in items), re.I)


def eligible_patterns(regions: dict, key: str) -> list[str]:
    entry = countries(regions).get(key or {}, {})
    return _str_list(entry, "eligible_phrases") if isinstance(entry, dict) else []


def phrase_in(text: str, phrase: str) -> bool:
    """True if phrase occurs in text (moved here so geo matching shares it)."""
    needle = (phrase or "").strip().lower()
    if not needle:
        return False
    hay = (text or "").lower()
    if re.search(r"[.\\+#]", needle) or " " in needle:
        return needle in hay
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))


def match_any(text: str, tokens) -> bool:
    return any(phrase_in(text or "", token) for token in tokens or [] if token)


def canonical_country(text: str, regions: dict) -> str | None:
    """Display name of the first dataset country mentioned in text, if any."""
    for key in countries(regions):
        if match_any(text, match_names(regions, key)):
            return display_name(regions, key) or None
    return None


def stoplist_tokens(regions: dict) -> set[str]:
    """Lowercase tokens the desk display skips when shortening locations.

    Country names, aliases, and province/state codes — never cities, which
    are the display output itself.
    """
    tokens: set[str] = set()
    for key, entry in countries(regions).items():
        if not isinstance(entry, dict):
            continue
        for token in _str_list(entry, "aliases"):
            tokens.add(token.lower())
        for code in _str_list(entry, "codes"):
            tokens.add(code.lower())
        if key:
            tokens.add(str(key).lower())
        if entry.get("display"):
            tokens.add(str(entry["display"]).lower())
    for word in _str_list({"region_words": (regions or {}).get("region_words")}, "region_words"):
        tokens.add(word.lower())
    return tokens
