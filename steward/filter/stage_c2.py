"""Stage C2: the deep read, run by Opus in cloud sessions.

The cluster side has three parts. Handoff: choose the trajectories, write them as shards with
their rendering and C1 result, and later merge the results. Branches: push the shards on
handoff/c2 and fetch result files from the results branches, both through a separate git
worktree so the main checkout is never switched. Launch: start one cloud session
per shard with at most N running; a session counts as running from launch until its results
branch appears on origin, and a launched shard is never launched again.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from steward.config import Settings
from steward.filter.rank import task_key
from steward.filter.redact import redact, redact_strings
from steward.filter.selection import load_by_ids
from steward.filter.stage_a import read_results as read_stage_a
from steward.filter.stage_b import read_results as read_stage_b
from steward.filter.stage_c1 import render_for_scoring
from steward.schema import (
    AXES,
    C2Input,
    StageC1Result,
    StageC2Output,
    StageC2Result,
)
from steward.store import append_jsonl, read_jsonl, read_meta, write_jsonl

log = logging.getLogger(__name__)


def top_two_axis_sum(result: StageC1Result) -> int:
    assert result.output is not None
    scores = sorted((result.output.scores[a].score for a in AXES), reverse=True)
    return scores[0] + scores[1]


def select_inputs(
    settings: Settings,
    c1: list[StageC1Result],
    agent_actions: dict[str, int],
    task_of: dict[str, str],
) -> list[StageC1Result]:
    """Top N per configured domain by the sum of the two highest axis scores, ties by agent
    actions; with one_per_task, only the best trajectory of each task is kept."""
    config = settings.stage_c2
    by_domain: dict[str, list[StageC1Result]] = defaultdict(list)
    for result in c1:
        if result.output is not None and result.domain in config.domains:
            by_domain[result.domain].append(result)
    chosen: list[StageC1Result] = []
    for domain in sorted(by_domain):
        ranked = sorted(
            by_domain[domain],
            key=lambda r: (
                -top_two_axis_sum(r),
                -agent_actions.get(r.trajectory_id, 0),
                r.trajectory_id,
            ),
        )
        seen: set[str] = set()
        picked: list[StageC1Result] = []
        for result in ranked:
            task = task_of.get(result.trajectory_id, result.trajectory_id)
            if config.one_per_task and task in seen:
                continue
            seen.add(task)
            picked.append(result)
            if len(picked) == config.per_domain:
                break
        chosen.extend(picked)
    return chosen


def build_shards(settings: Settings, c1_path: Path, b_path: Path, handoff_dir: Path) -> list[Path]:
    stage_a = {r.trajectory_id: r for r in read_stage_a(settings)}
    stage_b = {r.trajectory_id: r for r in read_stage_b(b_path)}
    c1 = read_jsonl(c1_path, StageC1Result)
    task_of = {
        meta.trajectory_id: task_key(meta)
        for source in sorted({r.source for r in c1})
        for meta in read_meta(settings.paths.normalized, source)
    }
    actions = {k: v.agent_actions for k, v in stage_a.items()}
    chosen = select_inputs(settings, c1, actions, task_of)
    by_id = {r.trajectory_id: r for r in chosen}
    lines: list[C2Input] = []
    for trajectory in load_by_ids(settings, list(by_id)):
        result = by_id[trajectory.trajectory_id]
        assert result.output is not None
        rendering = render_for_scoring(
            settings,
            trajectory,
            stage_a[trajectory.trajectory_id],
            stage_b.get(trajectory.trajectory_id),
        )
        lines.append(
            C2Input(
                trajectory_id=trajectory.trajectory_id,
                source=trajectory.source,
                domain=result.domain,
                rendering=redact(rendering),
                c1_output=type(result.output).model_validate(
                    redact_strings(result.output.model_dump())
                ),
            )
        )
    lines.sort(key=lambda line: (line.domain, line.trajectory_id))
    for old in handoff_dir.glob("shard_*.jsonl"):
        old.unlink()
    shards: list[list[C2Input]] = [[]]
    size = 0
    for line in lines:
        line_bytes = len(line.model_dump_json()) + 1
        full = len(shards[-1]) >= settings.stage_c2.shard_max_trajectories
        if shards[-1] and (full or size + line_bytes > settings.stage_c2.shard_max_bytes):
            shards.append([])
            size = 0
        shards[-1].append(line)
        size += line_bytes
    paths = []
    for number, shard in enumerate(s for s in shards if s):
        path = handoff_dir / f"shard_{number:03d}.jsonl"
        write_jsonl(path, shard)
        paths.append(path)
    log.info("c2 handoff: %d trajectories in %d shards", len(lines), len(paths))
    return paths


def split_shard(shard: Path, out_dir: Path) -> list[Path]:
    """Write one input file per trajectory of a shard, for one C2 subagent each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, line in enumerate(read_jsonl(shard, C2Input)):
        path = out_dir / f"{number:03d}.md"
        path.write_text(
            f"trajectory_id: {line.trajectory_id}\n\n"
            f"FIRST-PASS (C1) RESULT\n{line.c1_output.model_dump_json(indent=1)}\n\n"
            f"TRAJECTORY\n{line.rendering}\n"
        )
        paths.append(path)
    return paths


def c2_json_schema() -> str:
    return json.dumps(StageC2Output.model_json_schema(), indent=1)


def merge_results(handoff_dir: Path, results_dir: Path, out: Path) -> list[StageC2Result]:
    """Merge the cloud result files into one validated file, keeping only valid lines."""
    inputs: dict[str, C2Input] = {}
    for path in sorted(handoff_dir.glob("shard_*.jsonl")):
        for line in read_jsonl(path, C2Input):
            inputs[line.trajectory_id] = line
    merged: dict[str, StageC2Result] = {}
    invalid = 0
    for path in sorted(results_dir.glob("*.jsonl")):
        with path.open() as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    output = StageC2Output.model_validate_json(raw)
                except ValidationError:
                    invalid += 1
                    continue
                source_line = inputs.get(output.trajectory_id)
                if source_line is None:
                    invalid += 1
                    continue
                merged[output.trajectory_id] = StageC2Result(
                    trajectory_id=output.trajectory_id,
                    source=source_line.source,
                    domain=source_line.domain,
                    output=output,
                )
    results = [merged[k] for k in sorted(merged)]
    write_jsonl(out, results)
    log.info("c2 merge: %d valid results, %d invalid lines", len(results), invalid)
    return results


def read_results(settings: Settings) -> list[StageC2Result]:
    return read_jsonl(settings.paths.stage_c2 / "results.jsonl", StageC2Result)


HANDOFF_BRANCH = "handoff/c2"
MAX_FILE_BYTES = 20_000_000


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:3000]}")
    return result.stdout


def remote_branch_exists(repo: Path, branch: str) -> bool:
    return bool(git("ls-remote", "--heads", "origin", branch, cwd=repo).strip())


def push_handoff(repo: Path, shard_dirs: list[Path], message: str) -> str:
    """Commit the current shard files on handoff/c2 and push without rewriting history.

    The branch continues from its remote tip when it exists, with main merged in so the cloud
    session gets current code; otherwise it starts from main. Shard files in the given
    directories are replaced by the current ones.
    """
    files = [p for d in shard_dirs for p in sorted(d.glob("shard_*.jsonl"))]
    for path in files:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"{path} is over 20 MB")
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "handoff"
        if remote_branch_exists(repo, HANDOFF_BRANCH):
            git("fetch", "-q", "origin", HANDOFF_BRANCH, cwd=repo)
            git("worktree", "add", "--detach", str(worktree), "FETCH_HEAD", cwd=repo)
            git(
                "merge", "-q", "--no-edit", "-m", "Merge main into handoff/c2", "main", cwd=worktree
            )
        else:
            git("worktree", "add", "--detach", str(worktree), "main", cwd=repo)
        try:
            for directory in shard_dirs:
                stale = worktree / directory.relative_to(repo)
                for old in stale.glob("shard_*.jsonl"):
                    git("rm", "-q", "--cached", str(old.relative_to(worktree)), cwd=worktree)
                    old.unlink()
            for path in files:
                target = worktree / path.relative_to(repo)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                git("add", "-f", str(target.relative_to(worktree)), cwd=worktree)
            git("commit", "-q", "-m", message, cwd=worktree)
            git("push", "-q", "origin", f"HEAD:refs/heads/{HANDOFF_BRANCH}", cwd=worktree)
            sha = git("rev-parse", "HEAD", cwd=worktree).strip()
        finally:
            git("worktree", "remove", "--force", str(worktree), cwd=repo)
    log.info("pushed %d shard files to %s at %s", len(files), HANDOFF_BRANCH, sha)
    return sha


def fetch_results(repo: Path, branch_prefix: str, results_dir: Path) -> list[Path]:
    """Copy data/results/... from every origin branch with the prefix into results_dir."""
    git(
        "fetch",
        "-q",
        "origin",
        f"+refs/heads/{branch_prefix}*:refs/remotes/origin/{branch_prefix}*",
        cwd=repo,
    )
    refs = git(
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/remotes/origin/{branch_prefix}*",
        cwd=repo,
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    written = []
    relative = results_dir.relative_to(repo)
    for ref in refs.split():
        listing = git("ls-tree", "-r", "--name-only", ref, str(relative), cwd=repo)
        for name in listing.split():
            content = git("show", f"{ref}:{name}", cwd=repo)
            target = repo / name
            target.write_text(content)
            written.append(target)
    log.info("fetched %d result files from %s*", len(written), branch_prefix)
    return written


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
        f"exactly. Write results to {results_file}. Push results to branch {results_branch}. "
        "Nobody can answer questions: do not wait for input, and if blocked follow the "
        "'If something fails' section of that file."
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
