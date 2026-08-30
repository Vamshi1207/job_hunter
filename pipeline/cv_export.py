"""Build Word and Pages copies of a tailored HTML resume so the PDF is not the only edit path."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

SKIP_TAGS = {"script", "style", "head", "meta", "link", "title"}


def write_editable_exports(html_path: Path, pdf_path: Path | None = None) -> dict[str, Path]:
    """Write .docx and .pages next to the HTML resume. Returns paths that exist."""
    html_path = Path(html_path)
    if not html_path.is_file():
        return {}
    stem_path = html_path.with_suffix("")
    docx_path = Path(str(stem_path) + ".docx")
    pages_path = Path(str(stem_path) + ".pages")
    written: dict[str, Path] = {"html": html_path}
    try:
        html_to_docx(html_path, docx_path)
        written["docx"] = docx_path
    except Exception as exc:
        log.warning("Word export failed for %s: %s", html_path.name, exc)
    try:
        html_to_pages(html_path, pages_path, pdf_path=pdf_path)
        written["pages"] = pages_path
    except Exception as exc:
        log.warning("Pages export failed for %s: %s", html_path.name, exc)
    return written


def ensure_package_exports(folder: Path) -> dict[str, str | None]:
    """Create missing Word/Pages files for an application folder. Safe to call on list."""
    folder = Path(folder)
    html = next(iter(sorted(folder.glob("*_CV.html"))), None)
    pdf = next(iter(sorted(folder.glob("*_CV.pdf"))), None)
    names = {
        "html_name": html.name if html else None,
        "docx_name": None,
        "pages_name": None,
    }
    if html:
        write_editable_exports(html, pdf if pdf and pdf.is_file() else None)
        docx = html.with_suffix(".docx")
        pages = Path(str(html.with_suffix("")) + ".pages")
        names["docx_name"] = docx.name if docx.is_file() else None
        names["pages_name"] = pages.name if pages.is_file() else None
    else:
        docx = next(iter(sorted(folder.glob("*_CV.docx"))), None)
        pages = next(iter(sorted(folder.glob("*_CV.pages"))), None)
        names["docx_name"] = docx.name if docx else None
        names["pages_name"] = pages.name if pages else None
    return names


def html_to_docx(html_path: Path, docx_path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Inches, Pt, RGBColor

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    doc = Document()
    for section in doc.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    navy = RGBColor(0x1E, 0x3A, 0x5F)
    ink = RGBColor(0x1A, 0x1A, 0x1A)
    muted = RGBColor(0x5C, 0x5C, 0x5C)

    def _set_run(run, *, size=11, bold=False, italic=False, color=ink, name="Calibri"):
        run.bold = bold
        run.italic = italic
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.name = name
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:ascii"), name)
        rfonts.set(qn("w:hAnsi"), name)

    def _para(text, *, size=11, bold=False, italic=False, color=ink, space_after=4, space_before=0, align=None):
        text = (text or "").strip()
        if not text:
            return None
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.space_before = Pt(space_before)
        if align:
            p.alignment = align
        run = p.add_run(text)
        _set_run(run, size=size, bold=bold, italic=italic, color=color)
        return p

    body = soup.body or soup
    for el in body.find_all(True):
        if el.name in SKIP_TAGS:
            el.decompose()

    for el in body.children:
        if not isinstance(el, Tag):
            continue
        classes = el.get("class") or []
        if "header" in classes:
            _para(_text(el.select_one(".header-name")), size=22, bold=True, color=navy, space_after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
            _para(_text(el.select_one(".header-title")), size=11, italic=True, space_after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
            _para(_contact_text(el.select_one(".header-contact")), size=10, color=muted, space_after=10, align=WD_ALIGN_PARAGRAPH.CENTER)
            continue
        if "summary" in classes:
            _para(_text(el), size=11, space_after=10)
            continue
        if "section-heading" in classes:
            _para(_text(el), size=11, bold=True, color=navy, space_before=10, space_after=6)
            continue
        if "job-block" in classes or "school-block" in classes or "project-block" in classes:
            _add_job_block(el, _para, doc, _set_run, navy, ink, muted)
            continue
        if "skills-list" in classes:
            for row in _skill_rows(el):
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(2)
                label = p.add_run(row[0] + ": ")
                _set_run(label, size=10, bold=True, color=navy)
                items = p.add_run(row[1])
                _set_run(items, size=10, color=ink)
            continue
        if "keep-skills" in classes or "keep-education" in classes:
            heading = el.select_one(".section-heading")
            if heading:
                _para(_text(heading), size=11, bold=True, color=navy, space_before=10, space_after=6)
            skills = el.select_one(".skills-list")
            if skills:
                for row in _skill_rows(skills):
                    p = doc.add_paragraph()
                    p.paragraph_format.space_after = Pt(2)
                    label = p.add_run(row[0] + ": ")
                    _set_run(label, size=10, bold=True, color=navy)
                    items = p.add_run(row[1])
                    _set_run(items, size=10, color=ink)
            for block in el.select(".school-block, .job-block, .project-block"):
                _add_job_block(block, _para, doc, _set_run, navy, ink, muted)
            continue

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(docx_path))


def html_to_pages(html_path: Path, pages_path: Path, pdf_path: Path | None = None) -> None:
    """Write a .pages zip Pages on macOS can open (HTML + preview PDF + Word inside)."""
    html = html_path.read_text(encoding="utf-8")
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pages_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html)
        zf.writestr("Resume.html", html)
        zf.writestr(
            "Metadata/Properties.plist",
            (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>'
                "<key>title</key><string>Resume</string>"
                "</dict></plist>\n"
            ),
        )
        if pdf_path and Path(pdf_path).is_file():
            zf.write(pdf_path, "QuickLook/Preview.pdf")
        docx_path = html_path.with_suffix(".docx")
        if docx_path.is_file():
            zf.write(docx_path, "Document.docx")


def _text(el) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).split())


def _contact_text(el) -> str:
    if el is None:
        return ""
    parts = []
    for child in el.children:
        if isinstance(child, Tag):
            if "dot" in (child.get("class") or []):
                parts.append("·")
            else:
                t = _text(child)
                if t:
                    parts.append(t)
        else:
            t = str(child).strip()
            if t:
                parts.append(t)
    return " ".join(parts) if parts else _text(el)


def _skill_rows(el: Tag) -> list[tuple[str, str]]:
    labels = el.select(".skill-label")
    items = el.select(".skill-items")
    rows = []
    for label, item in zip(labels, items):
        rows.append((_text(label).rstrip(":"), _text(item)))
    return rows


def _add_job_block(el: Tag, _para, doc, _set_run, navy, ink, muted) -> None:
    from docx.shared import Pt

    headers = el.select(".job-header")
    for header in headers:
        left = header.select_one(".company-name, .job-title, .job-main")
        right = header.select_one(".job-place, .job-dates, .proj-link")
        left_text = _text(left) if left else _text(header)
        right_text = _text(right) if right else ""
        if not left_text and not right_text:
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        is_company = bool(header.select_one(".company-name"))
        run = p.add_run(left_text)
        _set_run(run, size=11, bold=is_company, italic=not is_company and bool(header.select_one(".job-title")), color=navy if is_company else ink)
        if right_text:
            gap = p.add_run("    " + right_text)
            _set_run(gap, size=9, color=muted)
    stack = el.select_one(".project-stack, .edu-focus")
    if stack:
        _para(_text(stack), size=9, italic=True, color=muted, space_after=2)
    for li in el.select("li"):
        point = li.select_one(".point") or li
        text = _text(point)
        if not text:
            continue
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run("• " + text)
        _set_run(run, size=10, color=ink)
