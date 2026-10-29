"""Ranking per domain and axis from C2 results with C1 fallback (brief 8)."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from steward.config import Settings
from steward.filter.render import estimate_tokens
from steward.filter.selection import load_by_ids
from steward.schema import (
    AXES,
    CRITERIA,
    Axis,
    C1Candidate,
    C2Candidate,
    CriterionScore,
    StageAResult,
    StageC1Result,
    StageC2Result,
    TrajectoryMeta,
)

log = logging.getLogger(__name__)

AGREEMENT_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}
TAG_CRITERIA = ("distributional_shift", "adversaries", "safe_exploration")

COLUMNS = [
    "rank",
    "trajectory_id",
    "source",
    "real_user",
    "history_author",
    "harness",
    "axis_score",
    "evidence_steps",
    "best_jump_in_step",
    "jump_in_phase",
    "history_tokens_at_jump_in",
    "buttons",
    "checkability",
    "right_behavior_agreement",
    "other_axis_scores",
    "tags",
]


class Scored(BaseModel):
    """A trajectory's final scores and candidates, from C2 when present, else from C1."""

    trajectory_id: str
    source: str
    domain: str
    from_c2: bool
    scores: dict[str, CriterionScore]
    c1_candidates: list[C1Candidate]
    c2_candidates: list[C2Candidate]
    honest_reporting_moment: bool
    authorization_boundary: bool


def combine(c1: list[StageC1Result], c2: list[StageC2Result]) -> list[Scored]:
    c2_by_id = {r.trajectory_id: r for r in c2}
    combined = []
    for result in c1:
        if result.output is None:
            continue
        deep = c2_by_id.get(result.trajectory_id)
        if deep is not None:
            combined.append(
                Scored(
                    trajectory_id=result.trajectory_id,
                    source=result.source,
                    domain=deep.domain,
                    from_c2=True,
                    scores=dict(deep.output.scores),
                    c1_candidates=[],
                    c2_candidates=deep.output.candidates,
                    honest_reporting_moment=deep.output.honest_reporting_moment,
                    authorization_boundary=deep.output.authorization_boundary,
                )
            )
        else:
            combined.append(
                Scored(
                    trajectory_id=result.trajectory_id,
                    source=result.source,
                    domain=result.domain,
                    from_c2=False,
                    scores=dict(result.output.scores),
                    c1_candidates=result.output.candidates,
                    c2_candidates=[],
                    honest_reporting_moment=result.output.honest_reporting_moment,
                    authorization_boundary=result.output.authorization_boundary,
                )
            )
    return combined


def all_unsafe(scored: Scored) -> bool:
    candidates = scored.c2_candidates or scored.c1_candidates
    return bool(candidates) and all(c.unsafe_precedent for c in candidates)


def best_c2(scored: Scored) -> C2Candidate | None:
    usable = [c for c in scored.c2_candidates if not c.unsafe_precedent]
    if not usable:
        return None
    return sorted(
        usable,
        key=lambda c: (
            c.conflicting_instruction,
            -AGREEMENT_RANK[c.right_behavior_agreement],
            c.step,
        ),
    )[0]


def best_c1(scored: Scored) -> C1Candidate | None:
    usable = [c for c in scored.c1_candidates if not c.unsafe_precedent]
    if not usable:
        return None
    return sorted(usable, key=lambda c: (c.conflicting_instruction, c.step))[0]


def task_key(meta: TrajectoryMeta) -> str:
    """The task a trajectory works on: its task environment, else its repository and commit."""
    if meta.task_env:
        return meta.task_env
    if meta.repo and meta.base_commit:
        return f"{meta.repo}@{meta.base_commit}"
    return meta.trajectory_id


def jump_in_step(scored: Scored) -> int | None:
    deep = best_c2(scored)
    if deep is not None:
        return deep.step
    shallow = best_c1(scored)
    return shallow.step if shallow is not None else None


def tags_for(scored: Scored, stage_a: StageAResult | None) -> dict[str, object]:
    kinds = sorted({t.kind for t in stage_a.tags}) if stage_a else []
    return {
        **{criterion: scored.scores[criterion].score for criterion in TAG_CRITERIA},
        "honest_reporting_moment": scored.honest_reporting_moment,
        "authorization_boundary": scored.authorization_boundary,
        "moment_kinds": kinds,
        "scored_by": "c2" if scored.from_c2 else "c1",
    }


def history_tokens(settings: Settings, wanted: dict[str, int]) -> dict[str, int]:
    """Estimated tokens of all steps before the jump-in step, computed from the trajectory."""
    tokens: dict[str, int] = {}
    for trajectory in load_by_ids(settings, list(wanted)):
        step = wanted[trajectory.trajectory_id]
        tokens[trajectory.trajectory_id] = sum(
            estimate_tokens(s.content) for s in trajectory.steps[:step]
        )
    return tokens


def rank_axis(
    scored: list[Scored],
    axis: Axis,
    agent_actions: dict[str, int],
    top_k: int,
) -> list[Scored]:
    eligible = [s for s in scored if s.scores[axis].score > 0 and not all_unsafe(s)]

    def key(s: Scored) -> tuple[object, ...]:
        deep = best_c2(s)
        agreement = AGREEMENT_RANK[deep.right_behavior_agreement if deep else None]
        return (
            -s.scores[axis].score,
            -agreement,
            -agent_actions.get(s.trajectory_id, 0),
            s.trajectory_id,
        )

    return sorted(eligible, key=key)[:top_k]


FINAL_COLUMNS = [
    "rank",
    "trajectory_id",
    "source",
    "task",
    "real_user",
    "history_author",
    "harness",
    "scored_by",
    "top_two_axis_sum",
    "best_axis",
    "axis_scores",
    "best_jump_in_step",
    "jump_in_phase",
    "checkability",
    "right_behavior_agreement",
    "agent_actions",
]


def top_two(scored: Scored) -> int:
    values = sorted((scored.scores[a].score for a in AXES), reverse=True)
    return values[0] + values[1]


def rank_final(
    scored: list[Scored], agent_actions: dict[str, int], task_of: dict[str, str], n: int
) -> list[Scored]:
    """The best n of a domain, one per task: C2-scored first, then by the two highest axis
    scores, agreement on the right behavior and agent actions."""

    def key(s: Scored) -> tuple[object, ...]:
        deep = best_c2(s)
        return (
            not s.from_c2,
            -top_two(s),
            -AGREEMENT_RANK[deep.right_behavior_agreement if deep else None],
            -agent_actions.get(s.trajectory_id, 0),
            s.trajectory_id,
        )

    picked: list[Scored] = []
    seen: set[str] = set()
    for item in sorted(scored, key=key):
        task = task_of.get(item.trajectory_id, item.trajectory_id)
        if top_two(item) == 0 or all_unsafe(item) or task in seen:
            continue
        seen.add(task)
        picked.append(item)
        if len(picked) == n:
            break
    return picked


def final_rows(
    ranked: list[Scored],
    metas: dict[str, TrajectoryMeta],
    agent_actions: dict[str, int],
    task_of: dict[str, str],
) -> list[dict[str, object]]:
    rows = []
    for position, scored in enumerate(ranked, start=1):
        meta = metas[scored.trajectory_id]
        deep = best_c2(scored)
        rows.append(
            {
                "rank": position,
                "trajectory_id": scored.trajectory_id,
                "source": scored.source,
                "task": task_of.get(scored.trajectory_id),
                "real_user": meta.real_user,
                "history_author": meta.history_author,
                "harness": meta.harness,
                "scored_by": "c2" if scored.from_c2 else "c1",
                "top_two_axis_sum": top_two(scored),
                "best_axis": max(AXES, key=lambda a: scored.scores[a].score),
                "axis_scores": json.dumps({a: scored.scores[a].score for a in AXES}),
                "best_jump_in_step": jump_in_step(scored),
                "jump_in_phase": deep.phase if deep else None,
                "checkability": deep.checkability if deep else None,
                "right_behavior_agreement": deep.right_behavior_agreement if deep else None,
                "agent_actions": agent_actions.get(scored.trajectory_id),
            }
        )
    return rows


def rows_for(
    ranked: list[Scored],
    axis: Axis,
    metas: dict[str, TrajectoryMeta],
    stage_a: dict[str, StageAResult],
    tokens: dict[str, int],
) -> list[dict[str, object]]:
    rows = []
    for position, scored in enumerate(ranked, start=1):
        meta = metas[scored.trajectory_id]
        deep = best_c2(scored)
        step = jump_in_step(scored)
        rows.append(
            {
                "rank": position,
                "trajectory_id": scored.trajectory_id,
                "source": scored.source,
                "real_user": meta.real_user,
                "history_author": meta.history_author,
                "harness": meta.harness,
                "axis_score": scored.scores[axis].score,
                "evidence_steps": json.dumps(scored.scores[axis].steps),
                "best_jump_in_step": step,
                "jump_in_phase": deep.phase if deep else None,
                "history_tokens_at_jump_in": (
                    deep.history_tokens if deep else tokens.get(scored.trajectory_id)
                ),
                "buttons": json.dumps(deep.buttons if deep else []),
                "checkability": deep.checkability if deep else None,
                "right_behavior_agreement": deep.right_behavior_agreement if deep else None,
                "other_axis_scores": json.dumps(
                    {a: scored.scores[a].score for a in AXES if a != axis}
                ),
                "tags": json.dumps(tags_for(scored, stage_a.get(scored.trajectory_id))),
            }
        )
    return rows


def run(
    settings: Settings,
    c1: list[StageC1Result],
    c2: list[StageC2Result],
    stage_a: list[StageAResult],
    metas: dict[str, TrajectoryMeta],
    domains: Sequence[str],
    out_root: Path | None = None,
) -> dict[tuple[str, str], int]:
    out_root = out_root or settings.paths.ranked
    stage_a_by_id = {r.trajectory_id: r for r in stage_a}
    actions = {r.trajectory_id: r.agent_actions for r in stage_a}
    scored = [s for s in combine(c1, c2) if set(CRITERIA) <= set(s.scores)]
    ranked: dict[tuple[str, Axis], list[Scored]] = {}
    for domain in domains:
        in_domain = [s for s in scored if s.domain == domain]
        for axis in AXES:
            ranked[(domain, axis)] = rank_axis(in_domain, axis, actions, settings.rank.top_k)
    need_tokens = {
        s.trajectory_id: step
        for rows in ranked.values()
        for s in rows
        if not s.from_c2 and (step := jump_in_step(s)) is not None
    }
    tokens = history_tokens(settings, need_tokens)
    counts: dict[tuple[str, str], int] = {}
    for (domain, axis), rows in ranked.items():
        frame = pd.DataFrame(rows_for(rows, axis, metas, stage_a_by_id, tokens), columns=COLUMNS)
        path = out_root / domain / f"{axis}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        counts[(domain, axis)] = len(frame)
        log.info("ranked %s/%s: %d rows", domain, axis, len(frame))
    task_of = {trajectory_id: task_key(meta) for trajectory_id, meta in metas.items()}
    n = settings.rank.final_per_domain
    for domain in settings.stage_c2.domains:
        in_domain = [s for s in scored if s.domain == domain]
        final = rank_final(in_domain, actions, task_of, n)
        frame = pd.DataFrame(final_rows(final, metas, actions, task_of), columns=FINAL_COLUMNS)
        path = out_root / domain / f"top_{n}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        counts[(domain, f"top_{n}")] = len(frame)
        log.info("final %s: %d rows", domain, len(frame))
    return counts
