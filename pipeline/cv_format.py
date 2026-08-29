"""Resume layout tokens from config.yaml `cv_format`. One source of truth per user."""

from __future__ import annotations

import re

from pipeline.config import Config

SECTION_RE = re.compile(
    r"<!--cv-section:([a-z]+)-->(.*?)<!--/cv-section:\1-->",
    re.DOTALL,
)

DENSITY = {
    "compact": {
        "line_height": "1.25",
        "name_size": "22pt",
        "body_size": "10pt",
        "page_margin": "0.45in 0.58in 0.42in 0.58in",
    },
    "comfortable": {
        "line_height": "1.35",
        "name_size": "26pt",
        "body_size": "10pt",
        "page_margin": "0.52in 0.62in 0.5in 0.62in",
    },
}

GOOGLE_FAMILIES = {
    "Fraunces": "Fraunces:opsz,wght@9..144,600;9..144,700",
    "IBM Plex Sans": "IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400",
    "Literata": "Literata:opsz,wght@7..72,600;7..72,700",
    "Source Serif 4": "Source+Serif+4:opsz,wght@8..60,600;8..60,700",
    "Source Sans 3": "Source+Sans+3:ital,wght@0,400;0,500;0,600;1,400",
    "Newsreader": "Newsreader:opsz,wght@6..72,600;6..72,700",
    "Public Sans": "Public+Sans:ital,wght@0,400;0,500;0,600;1,400",
}

DEFAULT_SECTION_ORDER = ["summary", "experience", "skills", "education", "projects"]


def _str(cfg: Config, key: str, default: str) -> str:
    val = cfg.get(f"cv_format.{key}", default)
    return default if val is None or val == "" else str(val)


def _bool(cfg: Config, key: str, default: bool) -> bool:
    val = cfg.get(f"cv_format.{key}", default)
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _list(cfg: Config, key: str, default: list[str]) -> list[str]:
    val = cfg.get(f"cv_format.{key}", default)
    if val is None:
        return list(default)
    if isinstance(val, str):
        return [p.strip() for p in val.split(",") if p.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    return list(default)


def keep_together(cfg: Config) -> list[str]:
    return [s.lower() for s in _list(cfg, "keep_together", ["skills", "education"])]


def section_order(cfg: Config) -> list[str]:
    order = [s.lower() for s in _list(cfg, "section_order", DEFAULT_SECTION_ORDER)]
    return order or list(DEFAULT_SECTION_ORDER)


def bullet_max_lines(cfg: Config) -> int:
    raw = cfg.get("cv_format.bullets.max_lines", 2)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def page_size(cfg: Config) -> str:
    raw = _str(cfg, "page_size", "letter").lower()
    return "A4" if raw == "a4" else "Letter"


def page_height_px(cfg: Config) -> float:
    return 11.69 * 96 if page_size(cfg) == "A4" else 11 * 96


def _keep_css(section: str, cfg: Config) -> str:
    return "avoid" if section in keep_together(cfg) else "auto"


def _font_links(name_font: str, body_font: str, enabled: bool, override: str) -> str:
    if override:
        href = override.strip()
        return (
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            f'    <link href="{href}" rel="stylesheet">'
        )
    if not enabled:
        return ""
    families = []
    for label, spec in GOOGLE_FAMILIES.items():
        if label in name_font or label in body_font:
            families.append(f"family={spec}")
    if not families:
        return ""
    href = "https://fonts.googleapis.com/css2?" + "&".join(families) + "&display=swap"
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'    <link href="{href}" rel="stylesheet">'
    )


def style_tokens(cfg: Config) -> dict[str, str]:
    density_name = _str(cfg, "density", "compact").lower()
    preset = DENSITY.get(density_name, DENSITY["compact"])
    header_align = _str(cfg, "header_align", "center").lower()
    if header_align not in {"left", "center"}:
        header_align = "center"
    body_align = _str(cfg, "body_align", "justify").lower()
    if body_align not in {"left", "justify"}:
        body_align = "justify"
    name_font = _str(cfg, "type.name_font", "Fraunces")
    body_font = _str(cfg, "type.body_font", "IBM Plex Sans")
    name_stack = f'{name_font}, "Iowan Old Style", Palatino, "Palatino Linotype", serif'
    body_stack = f'"{body_font}", "Helvetica Neue", Helvetica, Arial, sans-serif'
    if body_font.startswith('"'):
        body_stack = f'{body_font}, "Helvetica Neue", Helvetica, Arial, sans-serif'
    google_url = _str(cfg, "type.google_fonts_url", "")
    google_on = _bool(cfg, "type.google_fonts", True)
    show_tick = _bool(cfg, "show_section_tick", True)
    return {
        "CV_FONT_LINKS": _font_links(name_font, body_font, google_on, google_url),
        "CV_COLOR_INK": _str(cfg, "color.ink", "#1C1917"),
        "CV_COLOR_ACCENT": _str(cfg, "color.accent", "#1B365D"),
        "CV_COLOR_TICK": _str(cfg, "color.tick", "#0E7490"),
        "CV_COLOR_MUTED": _str(cfg, "color.muted", "#57534E"),
        "CV_NAME_FONT": name_stack,
        "CV_BODY_FONT": body_stack,
        "CV_NAME_SIZE": _str(cfg, "type.name_size", preset["name_size"]),
        "CV_BODY_SIZE": _str(cfg, "type.body_size", preset["body_size"]),
        "CV_LINE_HEIGHT": _str(cfg, "type.line_height", preset["line_height"]),
        "CV_PAGE_SIZE": page_size(cfg).lower(),
        "CV_PAGE_MARGIN": _str(cfg, "page_margin", preset["page_margin"]),
        "CV_HEADER_ALIGN": header_align,
        "CV_CONTACT_JUSTIFY": "center" if header_align == "center" else "flex-start",
        "CV_BODY_ALIGN": body_align,
        "CV_HYPHENS": "auto" if _bool(cfg, "hyphenate", True) else "none",
        "CV_KEEP_SKILLS": _keep_css("skills", cfg),
        "CV_KEEP_EDUCATION": _keep_css("education", cfg),
        "CV_TICK_DISPLAY": "block" if show_tick else "none",
    }


def apply_style_tokens(html: str, cfg: Config) -> str:
    for key, value in style_tokens(cfg).items():
        html = html.replace("{{" + key + "}}", value)
    return html


def reorder_sections(html: str, cfg: Config) -> str:
    matches = list(SECTION_RE.finditer(html))
    if not matches:
        return html
    found = {m.group(1): m.group(2) for m in matches}
    ordered = []
    used: set[str] = set()
    for name in section_order(cfg):
        if name in found:
            ordered.append(f"<!--cv-section:{name}-->{found[name]}<!--/cv-section:{name}-->")
            used.add(name)
    for name, body in found.items():
        if name not in used:
            ordered.append(f"<!--cv-section:{name}-->{body}<!--/cv-section:{name}-->")
            used.add(name)
    combined = "\n\n    ".join(ordered)
    return html[: matches[0].start()] + combined + html[matches[-1].end() :]


def apply_cv_format(html: str, cfg: Config) -> str:
    html = apply_style_tokens(html, cfg)
    return reorder_sections(html, cfg)


def tailor_layout_instructions(cfg: Config) -> str:
    pages = cfg.cv_pages
    lines = bullet_max_lines(cfg)
    keep = keep_together(cfg)
    order = section_order(cfg)
    keep_txt = ", ".join(keep) if keep else "none (sections may split across pages)"
    page_word = "page" if pages == 1 else "pages"
    return "\n".join(
        [
            f"- Target length is {pages} {page_word} (`config.yaml` cv_format.pages). "
            f"Keep bullets to at most {lines} line{'s' if lines != 1 else ''} so the HTML template fits. "
            f"Do not exceed {pages} {page_word}. Do not invent to fill space.",
            f"- Keep these sections on one page (do not strand a heading): {keep_txt}.",
            f"- Section order is {', '.join(order)} (`cv_format.section_order`).",
            f"- Header alignment: {_str(cfg, 'header_align', 'center')}. "
            f"Body text: {_str(cfg, 'body_align', 'justify')}.",
        ]
    )
