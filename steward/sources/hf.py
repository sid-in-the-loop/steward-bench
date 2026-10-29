"""Hugging Face download helper shared by loaders."""

from __future__ import annotations

import os
from pathlib import Path

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
