"""Give a scenario reviewed and unreviewed openings that differ only in what they say about
review.

The watched-or-not comparison is only clean if the two runs are otherwise identical. So the
drafter does not write two openings: it strips any statement about review from the existing
opening, without adding a word, and writes one sentence for each condition. The openings are
the stripped message followed by that sentence, or by nothing.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from steward.config import Settings
from steward.filter.batch import Estimate, check_budget
from steward.filter.render import estimate_tokens
from steward.filter.selection import load_by_ids
from steward.intervention.factors import OPENING_MESSAGE, halts
from steward.scenario.author import (
    ADVICE_WORDS,
    DRAFT_MAX_TOKENS,
    HINT_WORDS,
    distinctive_names,
    parse_reply,
)
from steward.scenario.spec import OversightMessages, ScenarioSpec, iter_specs, save_spec
from steward.scenario.subset import read_subset
from steward.schema import CostRecord, Trajectory
from steward.store import append_jsonl

log = logging.getLogger(__name__)

VOICE_EXAMPLES = 5
EXAMPLE_CHARS = 300
COMPLETION_TOKENS = 400
# Direct calls cost twice the batch price the model settings record.
DIRECT_PRICE_FACTOR = 2.0
FACTOR = "oversight_condition"


class OversightDraft(BaseModel):
    neutral: str
    reviewed: str
    unreviewed: str


class Outcome(BaseModel):
    scenario_id: str
    accepted: bool
    problems: list[str] = []
    prompt_tokens: int = 0
    completion_tokens: int = 0


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def only_removes(original: str, shortened: str) -> bool:
    """Whether the shortened text uses only words of the original, in the original order."""
    remaining = iter(words(original))
    return all(word in remaining for word in words(shortened))


def opening_of(spec: ScenarioSpec) -> str | None:
    return next((i.text for i in spec.interventions if i.id == OPENING_MESSAGE), None)


def needs_openings(spec: ScenarioSpec) -> bool:
    return spec.oversight is None and not halts(spec) and bool(opening_of(spec))


def problems_with(spec: ScenarioSpec, draft: OversightDraft) -> list[str]:
    """Why a draft cannot be used; empty when the three openings are a clean triple."""
    opening = opening_of(spec) or ""
    problems = []
    if not words(draft.neutral):
        problems.append("the neutral opening is empty")
    if not only_removes(opening, draft.neutral):
        problems.append("the neutral opening adds or reorders words of the original")
    for name, sentence in (("reviewed", draft.reviewed), ("unreviewed", draft.unreviewed)):
        if not words(sentence):
            problems.append(f"the {name} sentence is empty")
        if HINT_WORDS.search(sentence) or ADVICE_WORDS.search(sentence):
            problems.append(f"the {name} sentence hints or advises: {sentence!r}")
        if distinctive_names(spec.hazard.location) & distinctive_names(sentence):
            problems.append(f"the {name} sentence names the hazard: {sentence!r}")
    if words(draft.reviewed) == words(draft.unreviewed):
        problems.append("the reviewed and unreviewed sentences are the same")
    return problems


def with_openings(spec: ScenarioSpec, draft: OversightDraft) -> ScenarioSpec:
    """The scenario with its oversight block; the opening it runs with by default is kept."""
    neutral = draft.neutral.strip()
    oversight = OversightMessages(
        reviewed=f"{neutral} {draft.reviewed.strip()}",
        unreviewed=f"{neutral} {draft.unreviewed.strip()}",
        unstated=neutral,
    )
    factors = spec.factors if FACTOR in spec.factors else [*spec.factors, FACTOR]
    return ScenarioSpec.model_validate(
        {**spec.model_dump(), "oversight": oversight.model_dump(), "factors": factors}
    )


def brief(spec: ScenarioSpec, trajectory: Trajectory) -> str:
    said = [
        s.content[:EXAMPLE_CHARS]
        for s in trajectory.steps[: spec.source.takeover_step]
        if s.kind == "user"
    ][-VOICE_EXAMPLES:]
    earlier = "\n".join(f"- {m}" for m in said) or "- (none)"
    return f"MESSAGE:\n{opening_of(spec)}\n\nEARLIER MESSAGES FROM THIS USER:\n{earlier}"


def draft_openings(
    client: httpx.Client, model: str, prompt: str, spec: ScenarioSpec, trajectory: Trajectory
) -> tuple[ScenarioSpec | None, Outcome]:
    outcome = Outcome(scenario_id=spec.scenario_id, accepted=False)
    try:
        response = client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": brief(spec, trajectory)},
                ],
                "max_tokens": DRAFT_MAX_TOKENS,
                "temperature": 0,
                "reasoning": {"effort": "low"},
            },
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage") or {}
        outcome.prompt_tokens = int(usage.get("prompt_tokens", 0))
        outcome.completion_tokens = int(usage.get("completion_tokens", 0))
        text = body["choices"][0]["message"].get("content") or ""
        draft = OversightDraft.model_validate(parse_reply(text))
        outcome.problems = problems_with(spec, draft)
        if outcome.problems:
            return None, outcome
        updated = with_openings(spec, draft)
    except (ValidationError, ValueError, KeyError, httpx.HTTPError) as error:
        outcome.problems = [f"{type(error).__name__}: {str(error)[:200]}"]
        return None, outcome
    outcome.accepted = True
    return updated, outcome


def add_openings(
    settings: Settings, confirm: bool, max_usd: float, workers: int = 12
) -> list[Outcome]:
    """Draft reviewed and unreviewed openings for the subset's message scenarios that lack
    them, and write accepted ones into the scenario files."""
    chosen = read_subset(settings.paths.scenarios)
    todo = [
        s
        for s in iter_specs(settings.paths.scenarios)
        if s.scenario_id in chosen and needs_openings(s)
    ]
    prompt = (settings.paths.prompts / "scenario_oversight.md").read_text()
    trajectories = {
        t.trajectory_id: t for t in load_by_ids(settings, [s.source.trajectory_id for s in todo])
    }
    prompt_tokens = sum(
        estimate_tokens(prompt + brief(s, trajectories[s.source.trajectory_id])) for s in todo
    )
    completion_tokens = len(todo) * COMPLETION_TOKENS
    price = DIRECT_PRICE_FACTOR / 1_000_000
    usd = price * (
        prompt_tokens * settings.model.usd_per_mtok_prompt
        + completion_tokens * settings.model.usd_per_mtok_completion
    )
    estimate = Estimate(
        requests=len(todo),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=usd,
    )
    check_budget(settings, estimate, max_usd, confirm)
    client = httpx.Client(
        base_url=settings.model.api_base,
        headers={"Authorization": f"Bearer {os.environ[settings.model.api_key_env]}"},
        timeout=httpx.Timeout(180.0, connect=30.0),
    )

    def work(spec: ScenarioSpec) -> tuple[ScenarioSpec | None, Outcome]:
        trajectory = trajectories[spec.source.trajectory_id]
        return draft_openings(client, settings.model.model, prompt, spec, trajectory)

    outcomes = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for updated, outcome in pool.map(work, todo):
            outcomes.append(outcome)
            if updated is not None:
                save_spec(settings.paths.scenarios, updated)
            log.info("oversight %s: %s %s", outcome.scenario_id, outcome.accepted, outcome.problems)
    record_spend(settings, outcomes)
    return outcomes


def record_spend(settings: Settings, outcomes: list[Outcome]) -> None:
    prompt_tokens = sum(o.prompt_tokens for o in outcomes)
    completion_tokens = sum(o.completion_tokens for o in outcomes)
    price = DIRECT_PRICE_FACTOR / 1_000_000
    stamp = datetime.now(UTC)
    record = CostRecord(
        batch_id=f"direct-{stamp:%Y%m%dT%H%M%S}",
        stage="scenario_oversight",
        model=settings.model.model,
        requests=len(outcomes),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=price
        * (
            prompt_tokens * settings.model.usd_per_mtok_prompt
            + completion_tokens * settings.model.usd_per_mtok_completion
        ),
        submitted_at=stamp,
    )
    append_jsonl(settings.paths.costs, [record])


def outcomes_path(settings: Settings) -> Path:
    return settings.paths.data / "phase2" / "oversight_openings.jsonl"
