"""Terminal-Bench 2.0 leaderboard trials in Harbor ATIF form (HF harborframework/...).

Each submission is one tar.gz under archives/, read by streaming with tarfile. Only trials
with a non-empty agent/trajectory.json are loaded; the rest have only unstructured harness
stdout. A first pass lists the eligible trials, a seeded sample spread evenly across
submissions is drawn, and a second pass reads the sampled trials.
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.harbor_atif import parse_atif

log = logging.getLogger(__name__)

NAME = "terminal_bench"
REPO_URL = "https://huggingface.co/datasets/harborframework/terminal-bench-2-leaderboard"
MEMBER_PREFIX = "submissions/terminal-bench/2.0/"
ATIF_FILE = "agent/trajectory.json"
TRIAL_FILES = (ATIF_FILE, "result.json", "config.json")
MAX_TRIALS = 4000
LFS_POINTER_PREFIX = b"version https://git-lfs"
LFS_POINTER_MAX_BYTES = 1024


class TrialRef(BaseModel):
    submission: str
    job: str
    trial_name: str

    @property
    def member_dir(self) -> str:
        return f"{MEMBER_PREFIX}{self.submission}/{self.job}/{self.trial_name}"

    @property
    def native_id(self) -> str:
        return f"{self.submission}/{self.job}/{self.trial_name}"


class ArchiveListing(BaseModel):
    submission: str
    trials: list[TrialRef]
    metadata: dict[str, Any]


def trial_from_member(name: str) -> TrialRef | None:
    """'<prefix><submission>/<job...>/<trial>/agent/trajectory.json' -> TrialRef."""
    if not name.startswith(MEMBER_PREFIX) or not name.endswith("/" + ATIF_FILE):
        return None
    parts = name[len(MEMBER_PREFIX) : -len("/" + ATIF_FILE)].split("/")
    if len(parts) < 3:
        return None
    return TrialRef(submission=parts[0], job="/".join(parts[1:-1]), trial_name=parts[-1])


def _is_lfs_pointer(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bool:
    """The archives were packed without git-lfs, so some files are only pointer stubs."""
    if member.size > LFS_POINTER_MAX_BYTES:
        return False
    handle = archive.extractfile(member)
    return handle is not None and handle.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX


def list_archive(path: Path) -> ArchiveListing:
    trials: list[TrialRef] = []
    metadata: dict[str, Any] = {}
    submission = path.name.removesuffix(".tar.gz")
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            trial = trial_from_member(member.name)
            if trial is not None and member.size > 0 and not _is_lfs_pointer(archive, member):
                trials.append(trial)
                submission = trial.submission
            elif member.name.count("/") == 4 and member.name.endswith("/metadata.yaml"):
                handle = archive.extractfile(member)
                if handle is not None:
                    loaded = yaml.safe_load(handle.read().decode(errors="replace"))
                    metadata = loaded if isinstance(loaded, dict) else {}
    return ArchiveListing(submission=submission, trials=trials, metadata=metadata)


def allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    """Split `total` as evenly as possible; small submissions give their unused share away."""
    allocation: dict[str, int] = {}
    budget = total
    ordered = sorted(counts.items(), key=lambda item: (item[1], item[0]))
    for position, (name, available) in enumerate(ordered):
        share = budget // (len(ordered) - position)
        allocation[name] = min(available, share)
        budget -= allocation[name]
    return allocation


def sample_trials(listings: list[ArchiveListing], total: int, seed: int) -> set[str]:
    counts = {listing.submission: len(listing.trials) for listing in listings}
    allocation = allocate(counts, total)
    chosen: set[str] = set()
    for listing in listings:
        ids = sorted(trial.native_id for trial in listing.trials)
        rng = random.Random(f"{seed}:{listing.submission}")
        chosen.update(rng.sample(ids, allocation[listing.submission]))
    return chosen


def read_trials(
    path: Path, wanted: dict[str, TrialRef]
) -> Iterator[tuple[TrialRef, dict[str, bytes]]]:
    """Stream one archive and yield the files of each wanted trial."""
    by_dir = {trial.member_dir: trial for trial in wanted.values()}
    pending: dict[str, dict[str, bytes]] = {}
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            directory, _, filename = member.name.rpartition("/")
            relative = filename
            if directory.endswith("/agent"):
                directory = directory.removesuffix("/agent")
                relative = f"agent/{filename}"
            if directory not in by_dir or relative not in TRIAL_FILES:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            pending.setdefault(directory, {})[relative] = handle.read()
    for directory in sorted(pending):
        yield by_dir[directory], pending.pop(directory)


def _json(raw: bytes | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _get(data: dict[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


class TerminalBench(Source):
    info = SourceInfo(
        name=NAME,
        domain="terminal_devops",
        real_user=False,
        location="hf:harborframework/terminal-bench-2-leaderboard",
        license="apache-2.0",
        notes=(
            "75 submissions, 32,803 trials; 10,426 trials in 24 submissions have a structured "
            "Harbor ATIF agent/trajectory.json and are the only ones loaded. The other 22,377 "
            "trials have only unstructured harness stdout, no agent log, or a trajectory.json "
            "that is a git-lfs pointer stub (ClaudeCode__GLM-4.7, JJAgent__Multiple, 9 Judy "
            "trials), and are skipped. "
            f"A seeded sample of at most {MAX_TRIALS} ATIF trials is drawn, split evenly "
            "across submissions. history_author is result.json agent_info.model_info.name, "
            "else config agent.model_name; harness is agent_info.name. Agent configs and "
            "debug.json (litellm_params with api keys) are never read into meta."
        ),
    )

    def download(self, settings: Settings) -> None:
        """Shallow git clone without LFS, then one `git archive | gzip` per submission."""
        archives = self.raw_dir(settings) / "archives"
        archives.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.raw_dir(settings)) as tmp:
            clone = Path(tmp) / "repo"
            env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
            subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(clone)], check=True, env=env
            )
            for submission in sorted(p.name for p in (clone / MEMBER_PREFIX).iterdir()):
                target = archives / f"{submission}.tar.gz"
                if target.exists():
                    continue
                partial = target.with_name(target.name + ".part")
                with partial.open("wb") as out:
                    git = subprocess.Popen(
                        ["git", "-C", str(clone), "archive", "HEAD", MEMBER_PREFIX + submission],
                        stdout=subprocess.PIPE,
                    )
                    subprocess.run(["gzip", "-c"], stdin=git.stdout, stdout=out, check=True)
                    if git.wait() != 0:
                        raise RuntimeError(f"git archive failed for {submission}")
                partial.replace(target)
            shutil.copy(clone / "README.md", self.raw_dir(settings) / "README.md")

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        archives = sorted((self.raw_dir(settings) / "archives").glob("*.tar.gz"))
        listings = {path: list_archive(path) for path in archives}
        eligible = [listing for listing in listings.values() if listing.trials]
        chosen = sample_trials(eligible, MAX_TRIALS, settings.sampling.seed)
        counts: Counter[str] = Counter(eligible=sum(len(x.trials) for x in eligible))
        counts["sampled"] = len(chosen)
        for path, listing in listings.items():
            wanted = {t.native_id: t for t in listing.trials if t.native_id in chosen}
            if not wanted:
                continue
            for trial, files in read_trials(path, wanted):
                trajectory = self.build(trial, files, listing.metadata)
                counts["loaded" if trajectory else "skipped"] += 1
                if trajectory is not None:
                    yield trajectory
        log.info("%s: %s", NAME, dict(counts))

    def build(
        self, trial: TrialRef, files: dict[str, bytes], metadata: dict[str, Any]
    ) -> Trajectory | None:
        document = _json(files.get(ATIF_FILE))
        if not document:
            return None
        parsed = parse_atif(document)
        if not parsed.steps:
            return None
        result = _json(files.get("result.json"))
        config = _json(files.get("config.json")) or (_get(result, "config") or {})
        task_name = _str(result.get("task_name")) or trial.trial_name.rsplit("__", 1)[0]
        history_author = (
            _str(_get(result, "agent_info", "model_info", "name"))
            or _str(_get(config, "agent", "model_name"))
            or parsed.model_name
        )
        harness = _str(_get(result, "agent_info", "name")) or parsed.agent_name
        exception = result.get("exception_info")
        return Trajectory(
            trajectory_id=f"{NAME}:{trial.native_id}",
            source=NAME,
            domain="terminal_devops",
            real_user=False,
            history_author=history_author,
            harness=harness,
            repo=None,
            base_commit=None,
            task_env=f"terminal-bench-2:{task_name}",
            license=self.info.license,
            steps=parsed.steps,
            meta={
                "submission": trial.submission,
                "job": trial.job,
                "trial_name": trial.trial_name,
                "task_name": task_name,
                "reward": _get(result, "verifier_result", "rewards", "reward"),
                "exception_type": _str(_get(exception, "exception_type"))
                if isinstance(exception, dict)
                else None,
                "agent_version": _str(_get(result, "agent_info", "version")),
                "model_provider": _str(_get(result, "agent_info", "model_info", "provider")),
                "agent_display_name": _str(metadata.get("agent_display_name")),
                "task_git_commit": _str(_get(config, "task", "git_commit_id")),
                "atif_schema_version": parsed.schema_version,
            },
        )
