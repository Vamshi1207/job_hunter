"""Load experience-bank markdown for the tailor prompt."""

from __future__ import annotations

from pathlib import Path

SKIP_NAMES = {"readme.md", "example-project.md", "about-variants.example.md"}


def load_experience_bank(bank_dir: Path) -> str:
    if not bank_dir.is_dir():
        return ""

    parts = []
    for path in sorted(bank_dir.glob("*.md")):
        name = path.name.lower()
        if name in SKIP_NAMES or "example" in name:
            continue
        text = path.read_text().strip()
        if not text:
            continue
        parts.append(f"### {path.stem}\n\n{text}")
    return "\n\n".join(parts)


def load_optional(path: Path) -> str:
    if path.exists():
        return path.read_text()
    return ""
