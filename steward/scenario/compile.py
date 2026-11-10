"""Compile a scenario specification to a Harbor task directory, run it and score the trial."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from steward.config import Settings
from steward.execution.harbor_agent import RECORD_FILE, StewardAgent
from steward.execution.record import ExecutionRecord
from steward.scenario.spec import ScenarioSpec
from steward.scoring.score import Score, score

log = logging.getLogger(__name__)

REWARD_FILE = Path("verifier") / "reward.txt"


def compile_task(settings: Settings, spec: ScenarioSpec) -> Path:
    """A task_image scenario runs in its upstream task directory, tests included."""
    environment = spec.environment
    if environment.backend != "task_image":
        raise NotImplementedError("only task_image environments compile so far")
    assert environment.task_set and environment.task
    upstream = settings.paths.upstream_tasks / environment.task_set / environment.task
    if not upstream.is_dir():
        raise FileNotFoundError(f"task definition not downloaded: {upstream}")
    target = settings.paths.tasks / spec.key
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(upstream, target)
    return target


def run_reference(settings: Settings, spec: ScenarioSpec, spec_file: Path, reference: str) -> Path:
    """Run one reference execution through Harbor on Apptainer; returns the trial directory."""
    task_dir = compile_task(settings, spec)
    trials = settings.paths.trials / spec.key / reference
    if trials.exists():
        shutil.rmtree(trials)
    images = settings.paths.container_images
    command = [
        sys.executable,
        "-m",
        "harbor.cli.main",
        "trials",
        "start",
        "--path",
        str(task_dir),
        "--agent-import-path",
        StewardAgent.import_path(),
        "--agent-kwarg",
        f"spec_path={spec_file}",
        "--agent-kwarg",
        f"reference={reference}",
        "--agent-kwarg",
        f"task_dir={task_dir}",
        "--environment-type",
        "singularity",
        "--environment-kwarg",
        f"singularity_image_cache_dir={images / 'sif'}",
        "--trials-dir",
        str(trials),
    ]
    environment = {
        "APPTAINER_CACHEDIR": str(images / "cache"),
        "APPTAINER_TMPDIR": str(images / "tmp"),
    }
    for directory in ("sif", "cache", "tmp"):
        (images / directory).mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command, env={**os.environ, **environment}, capture_output=True, text=True, check=False
    )
    (trials / "harbor.log").write_text(completed.stdout + completed.stderr)
    trial_dirs = [p for p in trials.iterdir() if p.is_dir()]
    if completed.returncode != 0 or len(trial_dirs) != 1:
        raise RuntimeError(f"trial failed, see {trials / 'harbor.log'}")
    return trial_dirs[0]


def score_trial(spec: ScenarioSpec, trial_dir: Path) -> Score:
    """Score a finished trial from its execution record and the task verifier's reward."""
    record = ExecutionRecord.model_validate_json((trial_dir / "agent" / RECORD_FILE).read_text())
    reward_file = trial_dir / REWARD_FILE
    full_reward = reward_file.exists() and float(reward_file.read_text().strip() or 0) >= 1.0
    for check in [*spec.task_checks, *spec.safety_checks, *spec.disqualifying_violations]:
        if check.kind == "verifier_reward":
            record.probes[check.id] = full_reward
    return score(spec, record)
