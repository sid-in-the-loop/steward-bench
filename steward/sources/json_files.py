"""Reading JSON files that the downloaders may have gzipped (`.json` or `.json.gz`)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

JSON_SUFFIXES = (".json.gz", ".json")


def read_json(path: Path) -> Any:
    raw = gzip.decompress(path.read_bytes()) if path.name.endswith(".gz") else path.read_bytes()
    return json.loads(raw)


def json_stem(path: Path) -> str | None:
    """File name without `.json` or `.json.gz`, or None for other files."""
    for suffix in JSON_SUFFIXES:
        if path.name.endswith(suffix):
            return path.name[: -len(suffix)]
    return None


def json_files_by_stem(directory: Path, prefix: str) -> dict[str, Path]:
    """Map `<stem without prefix>` to its file, preferring `.json.gz` when both copies exist."""
    found: dict[str, Path] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.iterdir()):
        stem = json_stem(path)
        if stem is None or not stem.startswith(prefix):
            continue
        key = stem[len(prefix) :]
        if key not in found or path.name.endswith(".gz"):
            found[key] = path
    return dict(sorted(found.items()))
