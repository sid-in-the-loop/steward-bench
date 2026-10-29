"""Stage C1: criteria scoring of Stage B passes with Haiku on the Batch API (brief 6.3)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from steward.config import Settings
from steward.filter import batch
from steward.filter.render import candidate_steps, windowed_rendering
from steward.filter.selection import load_by_ids, select_with_floor
from steward.filter.stage_a import read_results as read_stage_a
from steward.schema import StageAResult, StageBResult, StageC1Output, StageC1Result, Trajectory
from steward.store import read_jsonl, write_jsonl

log = logging.getLogger(__name__)

OFFLINE_RANK = {"likely": 0, "unclear": 1, "unlikely": 2}


def prompt(settings: Settings) -> str:
    return (settings.paths.prompts / "stage_c1.md").read_text()


def select_inputs(
    settings: Settings, stage_b: list[StageBResult], stage_a: dict[str, StageAResult]
) -> list[StageBResult]:
    """Stage B passes ranked by offline rebuild, then Stage A prescore, then agent actions."""
    passes = [r for r in stage_b if r.output is not None and r.output.decision == "YES"]

    def key(r: StageBResult) -> tuple[object, ...]:
        a = stage_a[r.trajectory_id]
        assert r.output is not None
        return (
            OFFLINE_RANK[r.output.offline_rebuild],
            -a.prescore,
            -a.agent_actions,
            r.trajectory_id,
        )

    return select_with_floor(
        passes,
        domain_of=lambda r: r.domain,
        sort_key=key,
        top_n=settings.stage_c1.top_n,
        floor=settings.stage_c1.per_domain_floor,
    )


def render_for_scoring(
    settings: Settings, trajectory: Trajectory, stage_a: StageAResult, stage_b: StageBResult | None
) -> str:
    best = stage_b.output.best_steps if stage_b and stage_b.output else []
    candidates = candidate_steps(
        stage_a.tags,
        best,
        len(trajectory.steps),
        settings.render.max_windows,
        min_gap=settings.render.window_after,
    )
    return windowed_rendering(
        trajectory, candidates, settings.render, settings.stage_c1.rendering_token_cap
    )


def build_requests(
    settings: Settings,
    inputs: list[StageBResult],
    stage_a: dict[str, StageAResult],
) -> list[batch.BatchRequest]:
    by_id = {r.trajectory_id: r for r in inputs}
    system = prompt(settings)
    requests = []
    for trajectory in load_by_ids(settings, list(by_id)):
        trajectory_id = trajectory.trajectory_id
        rendering = render_for_scoring(
            settings, trajectory, stage_a[trajectory_id], by_id[trajectory_id]
        )
        requests.append(
            batch.BatchRequest(
                custom_id=trajectory_id,
                system=system,
                user=f"trajectory_id: {trajectory_id}\n\n{rendering}",
                max_tokens=settings.stage_c1.max_output_tokens,
                reasoning=settings.stage_c1.reasoning,
            )
        )
    return sorted(requests, key=lambda r: r.custom_id)


def parse_result(source_result: StageBResult, result: batch.BatchResult) -> StageC1Result:
    output = None
    error = result.error
    if result.text is not None:
        try:
            data = batch.parse_json_object(result.text)
            data["trajectory_id"] = source_result.trajectory_id
            output = StageC1Output.model_validate(data)
        except (ValueError, ValidationError) as exc:
            error = f"invalid output: {str(exc)[:300]}"
    return StageC1Result(
        trajectory_id=source_result.trajectory_id,
        source=source_result.source,
        domain=source_result.domain,
        output=output,
        error=error,
    )


def run(
    settings: Settings,
    inputs: list[StageBResult],
    out: Path,
    stage: str,
    confirm: bool,
    max_usd: float,
) -> list[StageC1Result]:
    stage_a = {r.trajectory_id: r for r in read_stage_a(settings)}
    pending = batch.submitted_ids(settings, stage)
    todo = [r for r in inputs if r.trajectory_id not in pending]
    if todo:
        requests = build_requests(settings, todo, stage_a)
        batch.check_prompt_sizes(requests, settings.model)
        job = batch.estimate(requests, settings.model)
        batch.check_budget(settings, job, max_usd, confirm)
        batch.submit(settings, stage, requests)
    collected = {r.custom_id: r for r in batch.collect(settings, stage)}
    results = [
        parse_result(r, collected[r.trajectory_id]) for r in inputs if r.trajectory_id in collected
    ]
    write_jsonl(out, results)
    ok = sum(1 for r in results if r.output is not None)
    log.info("%s: in %d, results %d, valid %d", stage, len(inputs), len(results), ok)
    return results


def read_results(path: Path) -> list[StageC1Result]:
    return read_jsonl(path, StageC1Result)
