"""The fixed sample of benchmark tasks that ablations run on.

Each environment gets a share of the sample in proportion to its share of the benchmark's
tasks, by largest remainder with ties to the environment first by name. Within an
environment the tasks are drawn from a seed fixed per environment over the task IDs in order,
so the same tasks always give the same sample.
"""

from __future__ import annotations

import random
from pathlib import Path

from steward.config import Settings
from steward.schedule import has_instruction_drift
from steward.session import SessionSpec
from steward.task import Task, tasks_root

SIZE = 30
SEED = 20261127
SAMPLE_FILE = "ablation_sample.txt"


def sample_path(settings: Settings) -> Path:
    return tasks_root(settings) / SAMPLE_FILE


def shares(counts: dict[str, int], size: int) -> dict[str, int]:
    total = sum(counts.values())
    exact = {name: count * size / total for name, count in counts.items()}
    given = {name: int(value) for name, value in exact.items()}
    by_remainder = sorted(exact, key=lambda name: (-(exact[name] - given[name]), name))
    for name in by_remainder[: size - sum(given.values())]:
        given[name] += 1
    return given


def draw(environments: dict[str, str], size: int = SIZE, seed: int = SEED) -> list[str]:
    """The sample from task IDs and the environment of each."""
    grouped: dict[str, list[str]] = {}
    for task in sorted(environments):
        grouped.setdefault(environments[task], []).append(task)
    counts = {name: len(tasks) for name, tasks in grouped.items()}
    chosen = [
        task
        for name, share in shares(counts, size).items()
        for task in random.Random(f"{seed}:{name}").sample(grouped[name], share)
    ]
    return sorted(chosen)


def benchmark_environments(tasks: list[tuple[Task, SessionSpec]]) -> dict[str, str]:
    return {task.id: task.domain for task, spec in tasks if has_instruction_drift(spec)}


def read_sample(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def write_sample(path: Path, tasks: list[str]) -> None:
    path.write_text("".join(f"{task}\n" for task in tasks))
