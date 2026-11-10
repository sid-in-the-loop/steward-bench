"""Fetching and reading raw source data: Hugging Face snapshots and dataset cards, pinned sparse
checkouts of GitHub repositories, and JSON files that may be gzipped.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download


def download_hf(repo_id: str, target: Path, allow_patterns: list[str] | None = None) -> Path:
    """Resumable snapshot download of a dataset repo; existing files are skipped."""
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=str(target),
        allow_patterns=allow_patterns,
        token=os.environ.get("HF_TOKEN"),
        max_workers=8,
    )
    return target


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


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def sparse_checkout(url: str, commit: str, patterns: list[str], target: Path) -> Path:
    """Check out only `patterns` of `url` at `commit` into `target`, then drop `.git`.

    An existing non-empty `target` is kept as is, so reruns never touch downloaded data. The
    clone goes to a sibling temp directory and is renamed into place only when complete.
    """
    if target.is_dir() and any(target.iterdir()):
        return target
    staging = target.with_name(target.name + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--filter=blob:none", "--no-checkout", "--sparse", url, str(staging))
    _git("sparse-checkout", "set", "--no-cone", *patterns, cwd=staging)
    _git("checkout", commit, cwd=staging)
    shutil.rmtree(staging / ".git")
    (target.parent / "UPSTREAM_COMMIT.txt").write_text(f"{url} {commit}\n")
    staging.rename(target)
    return target


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
