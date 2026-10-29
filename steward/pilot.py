"""The 40-trajectory pilot through B and C1, with token counts and a full-run projection."""

from __future__ import annotations

import json
import logging
from collections import Counter

from pydantic import BaseModel

from steward.config import Settings
from steward.filter import batch, stage_b, stage_c1
from steward.filter.selection import round_robin
from steward.filter.stage_a import read_results as read_stage_a
from steward.schema import CostRecord, StageAResult
from steward.store import read_jsonl

log = logging.getLogger(__name__)

PILOT_B = "pilot_b"
PILOT_C1 = "pilot_c1"


class StageNumbers(BaseModel):
    requests: int
    valid: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float

    @property
    def cost_per_trajectory(self) -> float:
        return self.cost_usd / self.requests if self.requests else 0.0

    @property
    def prompt_tokens_per_trajectory(self) -> float:
        return self.prompt_tokens / self.requests if self.requests else 0.0


class PilotNumbers(BaseModel):
    trajectories: int
    domains: dict[str, int]
    b: StageNumbers
    c1: StageNumbers
    b_yes: int
    full_b_inputs: int
    full_c1_inputs: int
    projected_b_usd: float
    projected_c1_usd: float

    @property
    def projected_total_usd(self) -> float:
        return self.projected_b_usd + self.projected_c1_usd


def pilot_inputs(settings: Settings) -> list[StageAResult]:
    survivors = [r for r in read_stage_a(settings) if r.passed]
    return round_robin(
        survivors,
        domain_of=lambda r: r.domain,
        sort_key=stage_b.stage_a_sort_key,
        n=settings.pilot.size,
    )


def stage_numbers(settings: Settings, stage: str, valid: int) -> StageNumbers:
    records = [r for r in read_jsonl(settings.paths.costs, CostRecord) if r.stage == stage]
    return StageNumbers(
        requests=sum(r.requests for r in records),
        valid=valid,
        prompt_tokens=sum(r.prompt_tokens for r in records),
        completion_tokens=sum(r.completion_tokens for r in records),
        cost_usd=sum(r.cost_usd for r in records),
    )


def run(settings: Settings, confirm: bool, max_usd: float) -> PilotNumbers:
    root = settings.paths.pilot
    inputs = pilot_inputs(settings)
    b_results = stage_b.run(settings, inputs, root / "stage_b.jsonl", PILOT_B, confirm, max_usd)
    # Every pilot trajectory goes through C1 so its cost is measured on the full pilot set.
    c1_results = stage_c1.run(
        settings, b_results, root / "stage_c1.jsonl", PILOT_C1, confirm, max_usd
    )
    b_numbers = stage_numbers(settings, PILOT_B, sum(1 for r in b_results if r.output))
    c1_numbers = stage_numbers(settings, PILOT_C1, sum(1 for r in c1_results if r.output))
    full_b = len(stage_b.full_inputs(settings))
    b_yes = sum(1 for r in b_results if r.output and r.output.decision == "YES")
    yes_rate = b_yes / len(b_results) if b_results else 0.0
    full_c1 = min(settings.stage_c1.top_n, round(full_b * yes_rate) + 1)
    numbers = PilotNumbers(
        trajectories=len(inputs),
        domains=dict(Counter(r.domain for r in inputs)),
        b=b_numbers,
        c1=c1_numbers,
        b_yes=b_yes,
        full_b_inputs=full_b,
        full_c1_inputs=full_c1,
        projected_b_usd=b_numbers.cost_per_trajectory * full_b,
        projected_c1_usd=c1_numbers.cost_per_trajectory * full_c1,
    )
    (root / "numbers.json").write_text(json.dumps(numbers.model_dump(), indent=2))
    return numbers


def estimate_only(settings: Settings) -> batch.Estimate:
    inputs = pilot_inputs(settings)
    requests = stage_b.build_requests(settings, inputs)
    return batch.estimate(requests, settings.model)
