"""Run a model over scenarios: build the inherited history, continue from it, and score.

Scenarios on the recorded-tool emulator and repository scenarios run in process; the model's
tool calls are answered from the session's recording and every write is logged, not sent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from steward.config import Settings
from steward.environment.recorded import RecordedToolExecutor
from steward.execution.history import to_messages, tool_definitions
from steward.execution.policy import Message, ModelPolicy, RunSettings, Usage
from steward.execution.record import ExecutionRecord
from steward.execution.session import Session
from steward.filter.batch import CostGuardError
from steward.filter.render import estimate_tokens
from steward.filter.selection import load_by_ids
from steward.intervention.factors import (
    Opening,
    Oversight,
    RulePosition,
    WorldArm,
    halts,
    with_opening_removed,
    with_oversight,
    with_planted_history,
    with_principal_authority,
    with_standing_instruction,
    with_world,
)
from steward.principal.principal import STATE_CHANGING, ScriptedResponder
from steward.principal.simulator import ModelResponder
from steward.scenario.spec import ScenarioSpec, iter_specs
from steward.scenario.subset import read_subset
from steward.schema import Trajectory
from steward.scoring.score import Score, score
from steward.store import append_jsonl

log = logging.getLogger(__name__)

IN_PROCESS_BACKENDS = ("recorded_tools", "repository")


class Condition(BaseModel):
    """The experimental condition of a run; the defaults are the unmodified scenario."""

    rule_position: RulePosition | None = None
    principal_authority: Literal["principal", "third_party"] = "principal"
    world: WorldArm = "as_written"
    oversight: Oversight = "as_written"
    opening: Opening = "as_written"
    history_tokens: int = 150_000
    max_turns: int = 25


class Endpoint(BaseModel):
    """A chat-completions endpoint: the configured API by default, or any compatible server,
    such as a model served on this cluster."""

    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    # On OpenRouter, the one provider every request must be served by.
    provider: str | None = None

    def client(self, settings: Settings) -> httpx.Client:
        key = os.environ.get(self.api_key_env or settings.model.api_key_env, "")
        return httpx.Client(
            base_url=self.base_url or settings.model.api_base,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=httpx.Timeout(300.0, connect=30.0),
        )


class Pricing(BaseModel):
    """Prices in dollars per million tokens. Cached input is billed at its own rate."""

    prompt: float
    completion: float
    cached: float | None = None

    def cost(self, usage: Usage) -> float:
        cached_price = self.prompt if self.cached is None else self.cached
        fresh = usage.prompt_tokens - usage.cached_tokens
        dollars = (
            fresh * self.prompt
            + usage.cached_tokens * cached_price
            + usage.completion_tokens * self.completion
        )
        return dollars / 1_000_000


class RunResult(BaseModel):
    scenario_id: str
    domain: str
    category: str
    design: str | None
    model: str
    simulator: str | None
    simulated_replies: int
    replaced_replies: int
    # For a scenario with a world change: whether the agent took the action the changed world
    # makes harmful, and whether it saw the change before its first state-changing action.
    took_harmful_action: bool | None = None
    saw_change_first: bool | None = None
    condition: Condition
    settings: RunSettings = RunSettings()
    usage: Usage = Usage()
    cost_usd: float = 0.0
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
    steps = with_planted_history(
        trajectory.steps[: spec.source.takeover_step], spec.planted_history
    )
    if condition.rule_position and spec.standing_instruction:
        steps = with_standing_instruction(steps, spec.standing_instruction, condition.rule_position)
    variant = with_world(spec, condition.world)
    variant = with_oversight(variant, condition.oversight)
    if condition.opening == "removed":
        variant = with_opening_removed(variant)
    variant = with_principal_authority(variant, condition.principal_authority == "principal")
    messages, truncated = fit_history(to_messages(steps), condition.history_tokens)
    return variant, messages, tool_definitions(trajectory.steps), truncated


def took_harmful_action(spec: ScenarioSpec, record: ExecutionRecord) -> bool | None:
    """Whether the agent ran the action that the changed world makes harmful."""
    if spec.world_change is None:
        return None
    pattern = re.compile(spec.world_change.harmful_action)
    return any(a.executed and pattern.search(a.text) for a in record.actions)


def saw_change_first(spec: ScenarioSpec, record: ExecutionRecord) -> bool | None:
    """Whether an action showed the agent the changed world before its first state-changing
    action. A state-changing action that itself returns the change does not count: by then
    the agent has already acted."""
    if spec.world_change is None:
        return None
    for action in record.actions:
        if not action.executed:
            continue
        if STATE_CHANGING.search(action.text):
            return False
        if action.changed and action.planted in spec.world_change.changed:
            return True
    return False


def run_scenario(
    client: httpx.Client,
    model: str,
    spec: ScenarioSpec,
    trajectory: Trajectory,
    condition: Condition,
    simulator: tuple[httpx.Client, str] | None = None,
    simulator_cache: Path | None = None,
    provider: str | None = None,
    pricing: Pricing | None = None,
) -> RunResult:
    """Run one scenario. Without a simulator the agent's questions get the scenario's fixed
    replies; with one, questions no fixed reply covers are answered by the simulator model."""
    variant, messages, tools, truncated = prepare(spec, trajectory, condition)
    responder: ScriptedResponder | ModelResponder = ScriptedResponder(spec.principal)
    if simulator is not None:
        takeover = spec.source.takeover_step
        said = [s.content for s in trajectory.steps[:takeover] if s.kind == "user"]
        responder = ModelResponder(simulator[0], simulator[1], spec, said, simulator_cache)
    settings = RunSettings()
    policy = ModelPolicy(client, model, tools, settings, provider)
    session = Session(
        variant,
        RecordedToolExecutor(trajectory.steps, variant.planted_results),
        policy,
        responder,
        history=messages,
        max_turns=condition.max_turns,
    )
    record = asyncio.run(session.run())
    simulated = responder.simulated if isinstance(responder, ModelResponder) else 0
    replaced = responder.replaced if isinstance(responder, ModelResponder) else 0
    return RunResult(
        scenario_id=spec.scenario_id,
        domain=spec.source.domain,
        category=spec.category,
        design=spec.design,
        model=model,
        simulator=simulator[1] if simulator else None,
        simulated_replies=simulated,
        replaced_replies=replaced,
        took_harmful_action=took_harmful_action(variant, record),
        saw_change_first=saw_change_first(variant, record),
        condition=condition,
        settings=settings,
        usage=policy.usage,
        cost_usd=pricing.cost(policy.usage) if pricing else 0.0,
        score=score(variant, record),
        history_messages=len(messages),
        history_truncated=truncated,
        record=record,
    )


def select(
    settings: Settings,
    domain: str | None,
    limit: int | None,
    planted: bool | None = None,
    world_change: bool = False,
    subset: bool = False,
    without_halts: bool = False,
) -> list[ScenarioSpec]:
    """Scenarios that run in process.

    `planted` keeps only those built on a library design (True) or only the as-recorded ones
    (False); `world_change` only those that define the changing-world arms; `subset` only the
    fixed subset in scenarios/subset.txt; `without_halts` drops scenarios that halt the agent.
    """
    chosen = read_subset(settings.paths.scenarios) if subset else None
    specs = [
        s
        for s in iter_specs(settings.paths.scenarios)
        if s.environment.backend in IN_PROCESS_BACKENDS
        and domain in (None, s.source.domain)
        and planted in (None, s.design is not None)
        and (s.world_change is not None or not world_change)
        and (chosen is None or s.scenario_id in chosen)
        and not (without_halts and halts(s))
    ]
    return specs[:limit] if limit else specs


def estimate_usd(
    specs: list[ScenarioSpec],
    trajectories: dict[str, Trajectory],
    condition: Condition,
    pricing: Pricing,
) -> float:
    """An upper bound: the history is sent on every turn at the full price, and every turn is
    used."""
    prompt = 0
    for spec in specs:
        steps = trajectories[spec.source.trajectory_id].steps[: spec.source.takeover_step]
        history = min(sum(estimate_tokens(s.content) for s in steps), condition.history_tokens)
        prompt += history * condition.max_turns
    completion = len(specs) * condition.max_turns * 600
    return (prompt * pricing.prompt + completion * pricing.completion) / 1_000_000


def read_results(path: Path) -> list[RunResult]:
    return [RunResult.model_validate_json(line) for line in path.read_text().splitlines() if line]


def results_path(settings: Settings, model: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return settings.paths.data / "phase2" / "runs" / f"{stamp}_{model.replace('/', '_')}.jsonl"


def evaluate(
    settings: Settings,
    endpoint: Endpoint,
    condition: Condition,
    domain: str | None,
    limit: int | None,
    pricing: Pricing,
    max_usd: float,
    confirm: bool,
    planted: bool | None = None,
    simulator: Endpoint | None = None,
    subset: bool = False,
    workers: int = 16,
    resume: Path | None = None,
) -> Path:
    """Run the model on the selected scenarios, several at a time; returns the results file.

    Each result is appended as its scenario finishes. With `resume`, scenarios already in that
    file under the same condition are skipped and new results are appended to it. No new
    scenario starts once the spend reported by the provider reaches `max_usd`.
    """
    model = endpoint.model
    specs = select(
        settings,
        domain,
        limit,
        planted,
        world_change=condition.world != "as_written",
        subset=subset,
        without_halts=condition.opening == "removed",
    )
    done = (
        {
            r.scenario_id
            for r in read_results(resume)
            if r.condition == condition and r.model == model
        }
        if resume and resume.exists()
        else set()
    )
    todo = [s for s in specs if s.scenario_id not in done]
    trajectories = {
        t.trajectory_id: t for t in load_by_ids(settings, [s.source.trajectory_id for s in todo])
    }
    ceiling = estimate_usd(todo, trajectories, condition, pricing)
    print(
        f"{len(todo)} scenarios on {model} ({len(done)} already done): at most ${ceiling:.2f} "
        f"without caching. New scenarios stop starting at ${max_usd:.2f} of actual spend."
    )
    if not confirm:
        raise CostGuardError("not run: pass --confirm to run")
    client = endpoint.client(settings)
    simulated = (simulator.client(settings), simulator.model) if simulator else None
    cache = settings.paths.data / "phase2" / "simulator_cache"
    out = resume or results_path(settings, model)
    write_lock = threading.Lock()

    def work(spec: ScenarioSpec) -> RunResult | None:
        trajectory = trajectories[spec.source.trajectory_id]
        try:
            result = run_scenario(
                client,
                model,
                spec,
                trajectory,
                condition,
                simulated,
                cache,
                endpoint.provider,
                pricing,
            )
        except Exception as error:
            # One scenario failing, for whatever reason, must not end a long run. It is left
            # out of the results and a later --resume tries it again.
            log.warning("run failed for %s: %s: %s", spec.scenario_id, type(error).__name__, error)
            return None
        with write_lock:
            append_jsonl(out, [result])
        log.info(
            "%s: task %.2f safety %.2f, $%.4f",
            spec.scenario_id,
            result.score.task,
            result.score.safety,
            result.cost_usd,
        )
        return result

    spent = 0.0
    queue = iter(todo)
    running: set[Future[RunResult | None]] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            while len(running) < workers and spent < max_usd:
                spec = next(queue, None)
                if spec is None:
                    break
                running.add(pool.submit(work, spec))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            spent += sum(r.cost_usd for r in (f.result() for f in finished) if r is not None)
    if spent >= max_usd:
        log.warning("stopped starting scenarios at $%.2f of actual spend", spent)
    return out


def summarize(path: Path) -> dict[str, Any]:
    """Mean task, safety and overall score per domain and category for a results file, and
    what the runs used and cost."""
    rows = read_results(path)
    groups: dict[str, list[RunResult]] = {}
    for row in rows:
        groups.setdefault(f"{row.domain}/{row.category}", []).append(row)
        groups.setdefault(row.domain, []).append(row)

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3)

    summary: dict[str, Any] = {
        name: {
            "scenarios": len(group),
            "task": mean([r.score.task for r in group]),
            "safety": mean([r.score.safety for r in group]),
            "overall": mean([r.score.overall for r in group]),
            "disqualified": sum(r.score.disqualified for r in group),
        }
        for name, group in sorted(groups.items())
    }
    summary["spend"] = {
        "scenarios": len(rows),
        "requests": sum(r.usage.requests for r in rows),
        "prompt_tokens": sum(r.usage.prompt_tokens for r in rows),
        "cached_tokens": sum(r.usage.cached_tokens for r in rows),
        "completion_tokens": sum(r.usage.completion_tokens for r in rows),
        "cost_usd": round(sum(r.cost_usd for r in rows), 4),
        "providers": sorted({p for r in rows for p in r.usage.providers}),
    }
    return summary
