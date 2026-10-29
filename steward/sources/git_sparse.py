"""Pinned sparse checkouts of GitHub repositories that hold trajectory files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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
