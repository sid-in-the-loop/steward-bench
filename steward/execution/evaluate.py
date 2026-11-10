"""Run a model over scenarios: build the inherited history, continue from it, and score.

Scenarios on the recorded-tool emulator and repository scenarios run in process; the model's
tool calls are answered from the session's recording and every write is logged, not sent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from steward.config import Settings
from steward.environment.recorded import RecordedToolExecutor
from steward.execution.history import to_messages, tool_definitions
from steward.execution.policy import Message, ModelPolicy
from steward.execution.record import ExecutionRecord
from steward.execution.session import Session
from steward.filter.batch import CostGuardError
from steward.filter.render import estimate_tokens
from steward.filter.selection import load_by_ids
from steward.intervention.factors import (
    DriftLevel,
    Recency,
    with_environment_drift,
    with_principal_authority,
    with_standing_instruction,
)
from steward.principal.principal import ScriptedResponder
from steward.scenario.spec import ScenarioSpec, iter_specs
from steward.schema import Trajectory
from steward.scoring.score import Score, score
from steward.store import append_jsonl

log = logging.getLogger(__name__)

IN_PROCESS_BACKENDS = ("recorded_tools", "repository")


class Condition(BaseModel):
    """The experimental condition of a run; the defaults are the unmodified scenario."""

    instruction_recency: Recency | None = None
    principal_authority: Literal["principal", "third_party"] = "principal"
    environment_drift: DriftLevel = "unchanged"
    history_tokens: int = 150_000
    max_turns: int = 25


class RunResult(BaseModel):
    scenario_id: str
    domain: str
    category: str
    model: str
    condition: Condition
    score: Score
    history_messages: int
    history_truncated: bool
    record: ExecutionRecord


def message_tokens(message: Message) -> int:
    return estimate_tokens(json.dumps(message, ensure_ascii=False))


def fit_history(messages: list[Message], budget_tokens: int) -> tuple[list[Message], bool]:
    """Keep the system messages, the first user message and the most recent turns that fit.

    A turn is a user message, or an assistant message with the tool results that answer it, so
    no tool result is left without its call.
    """
    if sum(message_tokens(m) for m in messages) <= budget_tokens:
        return messages, False
    first_user = next((i for i, m in enumerate(messages) if m["role"] == "user"), None)
    head = [m for i, m in enumerate(messages) if m["role"] == "system" or i == first_user]
    turns: list[list[Message]] = []
    for index, message in enumerate(messages):
        if message["role"] == "system" or index == first_user:
            continue
        if message["role"] == "tool" and turns:
            turns[-1].append(message)
        else:
            turns.append([message])
    remaining = budget_tokens - sum(message_tokens(m) for m in head)
    kept: list[list[Message]] = []
    for turn in reversed(turns):
        cost = sum(message_tokens(m) for m in turn)
        if cost > remaining:
            break
        kept.insert(0, turn)
        remaining -= cost
    return [*head, *(m for turn in kept for m in turn)], True


def prepare(
    spec: ScenarioSpec, trajectory: Trajectory, condition: Condition
) -> tuple[ScenarioSpec, list[Message], list[Message], bool]:
    """The scenario variant, the history messages and the tools for one condition."""
    steps = trajectory.steps[: spec.source.takeover_step]
    if condition.instruction_recency and spec.standing_instruction:
        steps = with_standing_instruction(
            steps, spec.standing_instruction, condition.instruction_recency
        )
    variant = with_environment_drift(spec, condition.environment_drift)
    variant = with_principal_authority(variant, condition.principal_authority == "principal")
    messages, truncated = fit_history(to_messages(steps), condition.history_tokens)
    return variant, messages, tool_definitions(trajectory.steps), truncated


def run_scenario(
    client: httpx.Client,
    model: str,
    spec: ScenarioSpec,
    trajectory: Trajectory,
    condition: Condition,
) -> RunResult:
    variant, messages, tools, truncated = prepare(spec, trajectory, condition)
    session = Session(
        variant,
        RecordedToolExecutor(trajectory.steps),
        ModelPolicy(client, model, tools),
        ScriptedResponder(spec.principal),
        history=messages,
        max_turns=condition.max_turns,
    )
    record = asyncio.run(session.run())
    return RunResult(
        scenario_id=spec.scenario_id,
        domain=spec.source.domain,
        category=spec.category,
        model=model,
        condition=condition,
        score=score(variant, record),
        history_messages=len(messages),
        history_truncated=truncated,
        record=record,
    )


def select(settings: Settings, domain: str | None, limit: int | None) -> list[ScenarioSpec]:
    specs = [
        s
        for s in iter_specs(settings.paths.scenarios)
        if s.environment.backend in IN_PROCESS_BACKENDS and domain in (None, s.source.domain)
    ]
    return specs[:limit] if limit else specs


def estimate_usd(
    specs: list[ScenarioSpec],
    trajectories: dict[str, Trajectory],
    condition: Condition,
    usd_per_mtok_prompt: float,
    usd_per_mtok_completion: float,
) -> float:
    """An upper bound: the history is sent on every turn, and every turn is used."""
    prompt = 0
    for spec in specs:
        steps = trajectories[spec.source.trajectory_id].steps[: spec.source.takeover_step]
        history = min(sum(estimate_tokens(s.content) for s in steps), condition.history_tokens)
        prompt += history * condition.max_turns
    completion = len(specs) * condition.max_turns * 600
    return (prompt * usd_per_mtok_prompt + completion * usd_per_mtok_completion) / 1_000_000


def evaluate(
    settings: Settings,
    model: str,
    condition: Condition,
    domain: str | None,
    limit: int | None,
    usd_per_mtok_prompt: float,
    usd_per_mtok_completion: float,
    max_usd: float,
    confirm: bool,
) -> Path:
    """Run the model on the selected scenarios; returns the results file."""
    specs = select(settings, domain, limit)
    trajectories = {
        t.trajectory_id: t for t in load_by_ids(settings, [s.source.trajectory_id for s in specs])
    }
    usd = estimate_usd(specs, trajectories, condition, usd_per_mtok_prompt, usd_per_mtok_completion)
    print(f"{len(specs)} scenarios on {model}: at most ${usd:.2f} (limit ${max_usd:.2f}).")
    if usd > max_usd:
        raise CostGuardError(f"estimate ${usd:.2f} exceeds --max-usd {max_usd:.2f}")
    if not confirm:
        raise CostGuardError("not run: pass --confirm to run")
    client = httpx.Client(
        base_url=settings.model.api_base,
        headers={"Authorization": f"Bearer {os.environ[settings.model.api_key_env]}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = settings.paths.data / "phase2" / "runs" / f"{stamp}_{model.replace('/', '_')}.jsonl"
    for spec in specs:
        try:
            result = run_scenario(
                client, model, spec, trajectories[spec.source.trajectory_id], condition
            )
        except (httpx.HTTPError, KeyError, ValueError) as error:
            log.warning("run failed for %s: %s", spec.scenario_id, error)
            continue
        append_jsonl(out, [result])
        log.info(
            "%s: task %.2f safety %.2f",
            spec.scenario_id,
            result.score.task,
            result.score.safety,
        )
    return out


def summarize(path: Path) -> dict[str, Any]:
    """Mean task, safety and overall score per domain and category for a results file."""
    rows = [RunResult.model_validate_json(line) for line in path.read_text().splitlines() if line]
    groups: dict[str, list[RunResult]] = {}
    for row in rows:
        groups.setdefault(f"{row.domain}/{row.category}", []).append(row)
        groups.setdefault(row.domain, []).append(row)

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3)

    return {
        name: {
            "scenarios": len(group),
            "task": mean([r.score.task for r in group]),
            "safety": mean([r.score.safety for r in group]),
            "overall": mean([r.score.overall for r in group]),
            "disqualified": sum(r.score.disqualified for r in group),
        }
        for name, group in sorted(groups.items())
    }
