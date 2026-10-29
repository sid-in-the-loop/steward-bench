"""Start Stage C2 cloud sessions, one per shard, at most N running at a time.

A session counts as running from launch until its results branch appears on origin. Launches
are logged to data/handoff/<handoff dir name>_launches.jsonl so a re-run never starts a shard
twice.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from steward.filter.c2_branches import HANDOFF_BRANCH, git
from steward.store import append_jsonl, read_jsonl

log = logging.getLogger(__name__)

SESSION_URL = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9]+")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Launch(BaseModel):
    shard: str
    results_branch: str
    results_file: str
    session_url: str | None
    launched_at: datetime


def prompt_for(shard: str, results_file: str, results_branch: str) -> str:
    return (
        f"Stage C2 for {shard} on branch {HANDOFF_BRANCH}. Follow docs/stage_c2_cloud.md "
        f"exactly. Write results to {results_file}. Push results to branch {results_branch}."
    )


def finished_branches(repo: Path, prefix: str) -> set[str]:
    heads = git("ls-remote", "--heads", "origin", f"{prefix}*", cwd=repo)
    return {line.split("refs/heads/", 1)[1] for line in heads.splitlines() if "refs/heads/" in line}


def start_session(repo: Path, prompt: str, log_dir: Path) -> str | None:
    """claude --cloud needs a terminal, so it runs under script(1); returns the session URL."""
    log_dir.mkdir(parents=True, exist_ok=True)
    typescript = log_dir / f"cloud_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.typescript"
    command = f"claude --cloud {shlex.quote(prompt)}"
    subprocess.run(
        ["script", "-qfc", command, str(typescript)],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    text = ANSI.sub("", typescript.read_text(errors="replace")) if typescript.exists() else ""
    match = SESSION_URL.search(text)
    return match.group(0) if match else None


def launch(
    repo: Path,
    handoff_dir: Path,
    results_dir: Path,
    branch_prefix: str,
    launches_path: Path,
    max_running: int,
    log_dir: Path,
) -> list[Launch]:
    launched = {entry.shard: entry for entry in read_jsonl(launches_path, Launch)}
    done = finished_branches(repo, branch_prefix)
    running = [e for e in launched.values() if e.results_branch not in done]
    started: list[Launch] = []
    for shard in sorted(handoff_dir.glob("shard_*.jsonl")):
        relative = str(shard.relative_to(repo))
        if relative in launched:
            continue
        if len(running) + len(started) >= max_running:
            break
        number = shard.stem.split("_")[-1]
        results_file = str((results_dir / shard.name).relative_to(repo))
        branch = f"{branch_prefix}{number}"
        url = start_session(repo, prompt_for(relative, results_file, branch), log_dir)
        entry = Launch(
            shard=relative,
            results_branch=branch,
            results_file=results_file,
            session_url=url,
            launched_at=datetime.now(UTC),
        )
        append_jsonl(launches_path, [entry])
        started.append(entry)
        log.info("c2 launch %s -> %s (%s)", relative, branch, url)
    return started
