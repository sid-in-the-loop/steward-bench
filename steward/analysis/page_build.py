"""Put the page's data object into the page template."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ASSIGNMENT = "window.STEWARD_RESULTS = "
QUOTES = "\"'`"


def object_end(text: str, start: int) -> int:
    """The index just past the object literal that opens at `start`, skipping braces inside
    strings."""
    depth = 0
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in QUOTES:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("the data object in the template is not closed")


def inject(template: str, data: dict[str, Any]) -> str:
    """The template with its `window.STEWARD_RESULTS = {...};` assignment replaced by the
    given object."""
    at = template.find(ASSIGNMENT)
    if at < 0:
        raise ValueError(f"the template has no `{ASSIGNMENT.strip()}` assignment")
    start = at + len(ASSIGNMENT)
    if template[start] != "{":
        raise ValueError("the template's data assignment is not an object literal")
    end = object_end(template, start)
    # A literal "</" would end the script element the object sits in.
    body = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=1).replace("</", "<\\/")
    return template[:start] + body + template[end:]


def build_page(template: Path, data: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(inject(template.read_text(), data))
    return out
