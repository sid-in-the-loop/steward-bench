"""The final list per domain: the best distinct tasks by Stage C2 score."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from steward.config import Settings
from steward.schema import AXES, Axis, C2Candidate, StageC2Result, TrajectoryMeta

log = logging.getLogger(__name__)

AGREEMENT_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}

# Build columns first (what a scenario is made from), then the columns used to choose.
COLUMNS = [
    "rank",
    "trajectory_id",
    "task",
    "jump_in_step",
    "primary_axis",
    "axis_scores",
    "available_shortcuts",
    "detection_method",
    "rebuild_dependencies",
    "evidence_steps",
    "excerpt",
    "history_author",
    "harness",
    "selection_score",
    "expected_behavior_agreement",
    "completable_without_shortcut",
    "conflicting_instruction",
    "history_tokens",
    "real_user",
    "source",
]


def task_key(meta: TrajectoryMeta) -> str:
    """The task a trajectory works on: its task environment, else its repository and commit."""
    if meta.task_env:
        return meta.task_env
    if meta.repo and meta.base_commit:
        return f"{meta.repo}@{meta.base_commit}"
    return meta.trajectory_id


def all_unsafe(result: StageC2Result) -> bool:
    candidates = result.output.jump_in_candidates
    return bool(candidates) and all(c.unsafe_precedent for c in candidates)


def best_candidate(result: StageC2Result) -> C2Candidate | None:
    """The jump-in point to use: no unsafe precedent, then no conflicting instruction, then the
    clearest expected behavior, then the earliest step."""
    usable = [c for c in result.output.jump_in_candidates if not c.unsafe_precedent]
    if not usable:
        return None
    return sorted(
        usable,
        key=lambda c: (
            c.conflicting_instruction,
            -AGREEMENT_RANK[c.expected_behavior_agreement],
            c.step,
        ),
    )[0]


def selection_score(result: StageC2Result) -> int:
    """The sum of the two highest axis scores, 0 to 6."""
    values = sorted((result.output.scores[a].score for a in AXES), reverse=True)
    return values[0] + values[1]


def primary_axis(result: StageC2Result) -> Axis:
    return max(AXES, key=lambda a: result.output.scores[a].score)


def rank_domain(
    results: list[StageC2Result],
    agent_actions: dict[str, int],
    task_of: dict[str, str],
    n: int,
) -> list[StageC2Result]:
    """The best n of a domain, one per task, by selection score, agreement on the expected
    behavior and agent actions. Trajectories with no axis score above 0, or whose jump-in
    candidates all follow unsafe behavior, are left out."""

    def key(result: StageC2Result) -> tuple[object, ...]:
        best = best_candidate(result)
        return (
            -selection_score(result),
            -AGREEMENT_RANK[best.expected_behavior_agreement if best else None],
            -agent_actions.get(result.trajectory_id, 0),
            result.trajectory_id,
        )

    picked: list[StageC2Result] = []
    seen: set[str] = set()
    for result in sorted(results, key=key):
        task = task_of.get(result.trajectory_id, result.trajectory_id)
        if selection_score(result) == 0 or all_unsafe(result) or task in seen:
            continue
        seen.add(task)
        picked.append(result)
        if len(picked) == n:
            break
    return picked


def rows_for(
    ranked: list[StageC2Result],
    metas: dict[str, TrajectoryMeta],
    task_of: dict[str, str],
) -> list[dict[str, object]]:
    rows = []
    for position, result in enumerate(ranked, start=1):
        meta = metas[result.trajectory_id]
        best = best_candidate(result)
        axis = primary_axis(result)
        evidence = result.output.scores[axis]
        rows.append(
            {
                "rank": position,
                "trajectory_id": result.trajectory_id,
                "task": task_of.get(result.trajectory_id),
                "jump_in_step": best.step if best else None,
                "primary_axis": axis,
                "axis_scores": json.dumps({a: result.output.scores[a].score for a in AXES}),
                "available_shortcuts": json.dumps(best.available_shortcuts if best else []),
                "detection_method": best.detection_method if best else None,
                "rebuild_dependencies": best.rebuild_dependencies if best else None,
                "evidence_steps": json.dumps(evidence.steps),
                "excerpt": evidence.excerpt,
                "history_author": meta.history_author,
                "harness": meta.harness,
                "selection_score": selection_score(result),
                "expected_behavior_agreement": (best.expected_behavior_agreement if best else None),
                "completable_without_shortcut": (
                    best.completable_without_shortcut if best else None
                ),
                "conflicting_instruction": best.conflicting_instruction if best else None,
                "history_tokens": best.history_tokens if best else None,
                "real_user": meta.real_user,
                "source": result.source,
            }
        )
    return rows


def list_path(root: Path, domain: str, n: int) -> Path:
    return root / domain / f"top_{n}.csv"


def run(
    settings: Settings,
    c2: list[StageC2Result],
    agent_actions: dict[str, int],
    metas: dict[str, TrajectoryMeta],
    out_root: Path | None = None,
) -> dict[str, int]:
    """Write data/ranked/<domain>/top_<n>.csv and a committed copy in reports/top_<n>/."""
    out_root = out_root or settings.paths.ranked
    task_of = {trajectory_id: task_key(meta) for trajectory_id, meta in metas.items()}
    n = settings.rank.final_per_domain
    counts: dict[str, int] = {}
    for domain in settings.stage_c2.domains:
        in_domain = [r for r in c2 if r.domain == domain]
        ranked = rank_domain(in_domain, agent_actions, task_of, n)
        frame = pd.DataFrame(rows_for(ranked, metas, task_of), columns=COLUMNS)
        path = list_path(out_root, domain, n)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        published = settings.paths.reports / f"top_{n}" / f"{domain}.csv"
        published.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(published, index=False)
        counts[domain] = len(frame)
        log.info("ranked %s: %d rows", domain, len(frame))
    return counts
