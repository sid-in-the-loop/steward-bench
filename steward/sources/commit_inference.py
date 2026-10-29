"""Infer a missing base commit from a public repository and the session's start time.

For a real-user session with a known public GitHub repository but no recorded commit, the base
commit is taken as the last commit on the default branch before the session's first timestamp.
Only commit history is fetched (a bare treeless clone). The method is recorded in the
trajectory's meta as base_commit_method so later phases can treat these commits as inferred.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from steward.config import Settings
from steward.store import DONE_FILE, META_FILE, STEPS_FILE, source_dir

log = logging.getLogger(__name__)

METHOD = "last_default_branch_commit_before_session_start"
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def mirror_path(settings: Settings, repo: str) -> Path:
    return settings.paths.raw / "_git" / f"{repo.replace('/', '__')}.git"


def fetch_history(settings: Settings, repo: str) -> Path | None:
    """Bare treeless clone of a public GitHub repo; None if it is private or gone."""
    path = mirror_path(settings, repo)
    if (path / "HEAD").exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--bare",
            "--filter=tree:0",
            "--single-branch",
            f"https://github.com/{repo}.git",
            str(path),
        ],
        env=GIT_ENV,
        capture_output=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        log.info("commit inference: cannot clone %s", repo)
        return None
    return path


def commit_before(mirror: Path, timestamp: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(mirror), "rev-list", "-1", f"--before={timestamp}", "HEAD"],
        env=GIT_ENV,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and len(sha) == 40 else None


def first_timestamps(directory: Path, wanted: set[str]) -> dict[str, str]:
    first: dict[str, str] = {}
    with gzip.open(directory / STEPS_FILE, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            trajectory_id = record["trajectory_id"]
            if trajectory_id in wanted and trajectory_id not in first and record.get("timestamp"):
                first[trajectory_id] = record["timestamp"]
    return first


def infer_for_source(settings: Settings, source: str, min_actions: int) -> int:
    """Fill missing base commits in data/normalized/<source>/meta.parquet; returns the count."""
    directory = source_dir(settings.paths.normalized, source)
    meta = pd.read_parquet(directory / META_FILE)
    needs = meta.repo.notna() & meta.base_commit.isna() & (meta.agent_actions >= min_actions)
    wanted = set(meta.loc[needs, "trajectory_id"])
    if not wanted:
        return 0
    starts = first_timestamps(directory, wanted)
    repos = sorted({str(r) for r in meta.loc[needs, "repo"]})
    with ThreadPoolExecutor(max_workers=8) as pool:
        mirrors = dict(
            zip(repos, pool.map(lambda r: fetch_history(settings, r), repos), strict=True)
        )
    filled = 0
    commits: list[str | None] = []
    metas: list[str] = []
    for row in meta.itertuples(index=False):
        trajectory_id = str(row.trajectory_id)
        commit = None if pd.isna(row.base_commit) else str(row.base_commit)
        meta_json = str(row.meta_json)
        mirror = mirrors.get(str(row.repo)) if trajectory_id in wanted else None
        start = starts.get(trajectory_id)
        if mirror is not None and start is not None:
            sha = commit_before(mirror, start)
            if sha is not None:
                commit = sha
                extra = json.loads(meta_json)
                extra["base_commit_method"] = METHOD
                meta_json = json.dumps(extra, sort_keys=True)
                filled += 1
        commits.append(commit)
        metas.append(meta_json)
    meta = meta.assign(base_commit=pd.Series(commits, dtype=object), meta_json=metas)
    tmp = directory / f"{META_FILE}.tmp"
    meta.to_parquet(tmp, index=False)
    tmp.replace(directory / META_FILE)
    (directory / DONE_FILE).write_text(f"{len(meta)}\n")
    log.info(
        "commit inference %s: %d of %d filled from %d repos",
        source,
        filled,
        len(wanted),
        len(repos),
    )
    return filled
