"""Stage B: cheap triage of Stage A survivors with Haiku on the Batch API (brief 6.2)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from steward.config import REAL_USER_DOMAINS_BY_RULE, Settings
from steward.filter import batch
from steward.filter.render import triage_summary
from steward.filter.selection import load_by_ids, select_with_floor, with_full_domains
from steward.filter.stage_a import read_results as read_stage_a
from steward.schema import Domain, StageAResult, StageBOutput, StageBResult
from steward.store import read_jsonl, write_jsonl

log = logging.getLogger(__name__)

STAGE = "stage_b"


def prompt(settings: Settings) -> str:
    return (settings.paths.prompts / "stage_b.md").read_text()


def stage_a_sort_key(result: StageAResult) -> tuple[object, ...]:
    return (-result.prescore, -result.agent_actions, result.trajectory_id)


def select_inputs(settings: Settings, survivors: list[StageAResult]) -> list[StageAResult]:
    chosen = select_with_floor(
        survivors,
        domain_of=lambda r: r.domain,
        sort_key=stage_a_sort_key,
        top_n=settings.stage_b.top_n,
        floor=settings.stage_b.per_domain_floor,
    )
    return with_full_domains(
        chosen, survivors, lambda r: r.domain, stage_a_sort_key, settings.stage_b.full_domains
    )


def build_requests(settings: Settings, inputs: list[StageAResult]) -> list[batch.BatchRequest]:
    tags = {r.trajectory_id: r.tags for r in inputs}
    system = prompt(settings)
    requests = []
    for trajectory in load_by_ids(settings, [r.trajectory_id for r in inputs]):
        summary = triage_summary(
            trajectory, tags[trajectory.trajectory_id], settings.stage_b.summary_token_budget
        )
        requests.append(
            batch.BatchRequest(
                custom_id=trajectory.trajectory_id,
                system=system,
                user=summary,
                max_tokens=settings.stage_b.max_output_tokens,
                reasoning=settings.stage_b.reasoning,
            )
        )
    return sorted(requests, key=lambda r: r.custom_id)


def effective_domain(stage_a: StageAResult, output: StageBOutput | None, by_rule: bool) -> Domain:
    """Sessions the domain rule could not decide take the domain returned by Stage B."""
    if by_rule or output is None:
        return stage_a.domain
    if output.domain in REAL_USER_DOMAINS_BY_RULE:
        return output.domain
    return stage_a.domain


def parse_result(
    stage_a: StageAResult, result: batch.BatchResult, decided_by_rule: bool
) -> StageBResult:
    output = None
    error = result.error
    if result.text is not None:
        try:
            output = StageBOutput.model_validate(batch.parse_json_object(result.text))
        except (ValueError, ValidationError) as exc:
            error = f"invalid output: {str(exc)[:300]}"
    return StageBResult(
        trajectory_id=stage_a.trajectory_id,
        source=stage_a.source,
        domain=effective_domain(stage_a, output, decided_by_rule),
        output=output,
        error=error,
    )


def domain_rule_flags(settings: Settings, ids: list[str]) -> dict[str, bool]:
    from steward.store import read_meta

    flags: dict[str, bool] = {}
    for source in sorted({i.split(":", 1)[0] for i in ids}):
        for meta in read_meta(settings.paths.normalized, source):
            flags[meta.trajectory_id] = meta.domain_decided_by_rule
    return flags


def run(
    settings: Settings,
    inputs: list[StageAResult],
    out: Path,
    stage: str,
    confirm: bool,
    max_usd: float,
) -> list[StageBResult]:
    """Submit what has not been submitted, collect everything, and write parsed results."""
    pending_ids = batch.submitted_ids(settings, stage)
    todo = [r for r in inputs if r.trajectory_id not in pending_ids]
    if todo:
        requests = build_requests(settings, todo)
        batch.check_prompt_sizes(requests, settings.model)
        job = batch.estimate(requests, settings.model)
        batch.check_budget(settings, job, max_usd, confirm)
        batch.submit(settings, stage, requests)
    collected = {r.custom_id: r for r in batch.collect(settings, stage)}
    flags = domain_rule_flags(settings, [r.trajectory_id for r in inputs])
    results = [
        parse_result(r, collected[r.trajectory_id], flags.get(r.trajectory_id, True))
        for r in inputs
        if r.trajectory_id in collected
    ]
    write_jsonl(out, results)
    passed = sum(1 for r in results if r.output and r.output.decision == "YES")
    log.info("%s: in %d, results %d, YES %d", stage, len(inputs), len(results), passed)
    return results


def full_inputs(settings: Settings) -> list[StageAResult]:
    survivors = [r for r in read_stage_a(settings) if r.passed]
    return select_inputs(settings, survivors)


def read_results(path: Path) -> list[StageBResult]:
    return read_jsonl(path, StageBResult)
