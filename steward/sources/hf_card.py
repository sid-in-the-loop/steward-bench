"""Read fields from a Hugging Face dataset card (README.md YAML front matter)."""

from __future__ import annotations

import re
from pathlib import Path

LICENSE_LINE = re.compile(r"^license:\s*['\"]?([^'\"\s]+)", re.MULTILINE)


def card_license(readme: Path) -> str | None:
    """The `license:` value from the card's front matter, or None if there is none."""
    if not readme.is_file():
        return None
    text = readme.read_text(errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    front = text[3:end] if end != -1 else ""
    match = LICENSE_LINE.search(front)
    return match.group(1) if match else None
