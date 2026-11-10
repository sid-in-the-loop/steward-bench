"""Run the authoring loop over a domain until enough scenarios pass their gates."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from steward.config import Settings
from steward.filter.batch import Estimate, check_budget
from steward.filter.rank import best_candidate, selection_score
from steward.filter.render import estimate_tokens
from steward.filter.selection import load_by_ids
from steward.scenario.author import (
    Draft,
    assemble,
    best_category,
    describe_failure,
    gate_recorded,
    lint,
    request_draft,
    session_brief,
)
from steward.scenario.spec import ScenarioSpec, iter_specs, save_spec
from steward.schema import Axis, C2Candidate, CostRecord, StageC2Result, Trajectory
from steward.store import append_jsonl, read_jsonl

log = logging.getLogger(__name__)

ATTEMPTS = 3
# Direct calls cost twice the batch price the model settings record.
DIRECT_PRICE_FACTOR = 2.0
DRAFT_COMPLETION_TOKENS = 1500

Gate = Callable[[ScenarioSpec, Trajectory], list[str]]


class Outcome(BaseModel):
    trajectory_id: str
    category: str
    accepted: bool
    attempts: int
    problems: list[str] = []
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Candidate(BaseModel):
    result: StageC2Result
    step: C2Candidate
    categories: list[Axis]


def candidates(
    results: list[StageC2Result], sources: set[str], tried: dict[str, set[str]]
) -> list[Candidate]:
    """Deep-read sessions from the given sources with a usable takeover step and a category
    scoring at least 2, strongest first.

    A session that was rejected before is repaired in order: a category it has not been tried
    with, then its next takeover step.
    """
    found = []
    for result in sorted(results, key=lambda r: (-selection_score(r), r.trajectory_id)):
        if result.source not in sources:
            continue
        step = best_candidate(result)
        categories = best_category(result)
        attempted = tried.get(result.trajectory_id, set())
        untried = [c for c in categories if c not in attempted]
        if attempted and not untried:
            usable = [c for c in result.output.jump_in_candidates if not c.unsafe_precedent]
            later = [c for c in usable if step is not None and c.step != step.step]
            step, untried = (later[0], categories) if later else (None, [])
        if step is not None and untried:
            found.append(Candidate(result=result, step=step, categories=untried))
    return found


def allocate(pool: list[Candidate]) -> list[tuple[Candidate, Axis]]:
    """Give each session the category it supports that has the fewest scenarios so far, so
    rare categories are not crowded out by common ones."""
    counts: Counter[str] = Counter()
    allocated = []
    for candidate in pool:
        category = min(
            candidate.categories, key=lambda c: (counts[c], candidate.categories.index(c))
        )
        counts[category] += 1
        allocated.append((candidate, category))
    return allocated


def author_one(
    client: httpx.Client,
    model: str,
    prompt: str,
    trajectory: Trajectory,
    candidate: Candidate,
    category: Axis,
    environment: Mapping[str, object],
    gate: Gate,
) -> tuple[ScenarioSpec | None, Outcome]:
    """Draft, assemble, lint and gate one session, feeding each failure back to the drafter."""
    brief = session_brief(trajectory, candidate.result, candidate.step, category)
    outcome = Outcome(
        trajectory_id=trajectory.trajectory_id, category=category, accepted=False, attempts=0
    )
    feedback = ""
    for _ in range(ATTEMPTS):
        outcome.attempts += 1
        try:
            draft, usage = request_draft(client, model, prompt, brief, feedback)
            outcome.prompt_tokens += int(usage.get("prompt_tokens", 0))
            outcome.completion_tokens += int(usage.get("completion_tokens", 0))
            spec = build(trajectory, candidate, category, draft, environment)
            problems = lint(spec) or gate(spec, trajectory)
        except (ValidationError, ValueError, KeyError, re.error, httpx.HTTPError) as error:
            problems = [describe_failure(error)]
        if not problems:
            outcome.accepted = True
            outcome.problems = []
            return spec, outcome
        outcome.problems = problems
        feedback = describe_failure(problems)
    return None, outcome


def build(
    trajectory: Trajectory,
    candidate: Candidate,
    category: Axis,
    draft: Draft,
    environment: Mapping[str, object],
) -> ScenarioSpec:
    evidence = candidate.result.output.scores[category].steps
    return assemble(trajectory, candidate.step, category, evidence, draft, dict(environment))


def author_domain(
    settings: Settings,
    domain: str,
    results: list[StageC2Result],
    sources: set[str],
    environment: Mapping[str, object],
    gate: Gate,
    target: int,
    confirm: bool,
    max_usd: float,
    workers: int = 12,
) -> list[Outcome]:
    """Author scenarios for one domain until `target` are accepted or the pool is exhausted."""
    domain_of = {r.trajectory_id: r.domain for r in results}
    done = {s.source.trajectory_id for s in iter_specs(settings.paths.scenarios)}
    have = sum(1 for t in done if domain_of.get(t) and t.split(":")[0] in sources)
    previous = outcomes_path(settings, domain)
    tried: dict[str, set[str]] = {}
    for outcome in read_jsonl(previous, Outcome) if previous.exists() else []:
        tried.setdefault(outcome.trajectory_id, set()).add(outcome.category)
    pool = [c for c in candidates(results, sources, tried) if c.result.trajectory_id not in done]
    plan = allocate(pool)
    prompt = (settings.paths.prompts / "scenario_author.md").read_text()
    trajectories = {
        t.trajectory_id: t for t in load_by_ids(settings, [c.result.trajectory_id for c, _ in plan])
    }
    briefs = [
        session_brief(trajectories[c.result.trajectory_id], c.result, c.step, category)
        for c, category in plan
    ]
    price = DIRECT_PRICE_FACTOR / 1_000_000
    prompt_tokens = sum(estimate_tokens(prompt + b) for b in briefs) * ATTEMPTS
    completion_tokens = len(briefs) * ATTEMPTS * DRAFT_COMPLETION_TOKENS
    usd = price * (
        prompt_tokens * settings.model.usd_per_mtok_prompt
        + completion_tokens * settings.model.usd_per_mtok_completion
    )
    check_budget(
        settings,
        Estimate(
            requests=len(briefs) * ATTEMPTS,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd=usd,
        ),
        max_usd,
        confirm,
    )
    client = httpx.Client(
        base_url=settings.model.api_base,
        headers={"Authorization": f"Bearer {os.environ[settings.model.api_key_env]}"},
        timeout=httpx.Timeout(180.0, connect=30.0),
    )

    def work(item: tuple[Candidate, Axis]) -> tuple[ScenarioSpec | None, Outcome]:
        candidate, category = item
        trajectory = trajectories[candidate.result.trajectory_id]
        return author_one(
            client, settings.model.model, prompt, trajectory, candidate, category, environment, gate
        )

    outcomes: list[Outcome] = []
    with ThreadPoolExecutor(max_workers=workers) as pool_executor:
        for spec, outcome in pool_executor.map(work, plan):
            outcomes.append(outcome)
            if spec is not None and have < target:
                save_spec(settings.paths.scenarios, spec)
                have += 1
            log.info(
                "author %s %s: %s after %d attempts %s",
                outcome.trajectory_id,
                outcome.category,
                "accepted" if outcome.accepted else "rejected",
                outcome.attempts,
                outcome.problems[:1],
            )
    record_spend(settings, outcomes)
    append_jsonl(previous, outcomes)
    return outcomes


def record_spend(settings: Settings, outcomes: list[Outcome]) -> None:
    prompt_tokens = sum(o.prompt_tokens for o in outcomes)
    completion_tokens = sum(o.completion_tokens for o in outcomes)
    price = DIRECT_PRICE_FACTOR / 1_000_000
    cost = price * (
        prompt_tokens * settings.model.usd_per_mtok_prompt
        + completion_tokens * settings.model.usd_per_mtok_completion
    )
    stamp = datetime.now(UTC)
    append_jsonl(
        settings.paths.costs,
        [
            CostRecord(
                batch_id=f"direct-{stamp:%Y%m%dT%H%M%S}",
                stage="scenario_author",
                model=settings.model.model,
                requests=sum(o.attempts for o in outcomes),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                submitted_at=stamp,
            )
        ],
    )


def outcomes_path(settings: Settings, domain: str) -> Path:
    return settings.paths.data / "phase2" / f"authoring_{domain}.jsonl"


def recorded_gate(spec: ScenarioSpec, trajectory: Trajectory) -> list[str]:
    return gate_recorded(spec, trajectory)
