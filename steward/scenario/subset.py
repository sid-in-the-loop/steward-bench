"""The fixed subset of scenarios that the paired analyses run on."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from steward.scenario.spec import ScenarioSpec
from steward.schema import AXES, BENCHMARK_DOMAINS

SUBSET_FILE = "subset.txt"
PER_CELL = 5
IN_PROCESS_BACKENDS = ("recorded_tools", "repository")


def choose_subset(specs: list[ScenarioSpec], per_cell: int = PER_CELL) -> list[ScenarioSpec]:
    """Up to `per_cell` scenarios for every domain and category, at most one per session.

    Scenarios built on a library design come first, then by key, so the choice is the same on
    every run. Within a domain the rarest category chooses first, so a category with few
    scenarios is not left without sessions.
    """
    runnable = [s for s in specs if s.environment.backend in IN_PROCESS_BACKENDS]
    chosen: list[ScenarioSpec] = []
    for domain in BENCHMARK_DOMAINS:
        in_domain = [s for s in runnable if s.source.domain == domain]
        counts = Counter(s.category for s in in_domain)
        used_sessions: set[str] = set()
        for category in sorted(AXES, key=lambda c: (counts[c], AXES.index(c))):
            pool = sorted(
                (s for s in in_domain if s.category == category),
                key=lambda s: (s.design is None, s.key),
            )
            taken = 0
            for spec in pool:
                if taken == per_cell:
                    break
                if spec.source.trajectory_id in used_sessions:
                    continue
                used_sessions.add(spec.source.trajectory_id)
                chosen.append(spec)
                taken += 1
    return sorted(chosen, key=lambda s: (s.source.domain, s.category, s.key))


def write_subset(root: Path, specs: list[ScenarioSpec]) -> Path:
    path = root / SUBSET_FILE
    path.write_text("".join(f"{s.scenario_id}\n" for s in specs))
    return path


def read_subset(root: Path) -> set[str]:
    return {line for line in (root / SUBSET_FILE).read_text().splitlines() if line}
