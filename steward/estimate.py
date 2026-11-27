"""A rough cost of planned sessions: sessions x the average tokens a session used x price.

The tokens come from the model's own stored sessions where it has any, otherwise from the
average of the stored models' profiles. The simulator's come from stored sessions whose people
it played, otherwise from a fixed assumption."""

from __future__ import annotations

from pydantic import BaseModel

from steward.execution.policy import Usage
from steward.prices import Pricing
from steward.session import SessionRun

# What the simulator is assumed to use per shift when no stored session counted it. Measured
# on 2026-11-26 with gpt-6-luna playing the people: 2 replies in 12 shifts (1.8 over 634
# gpt-6-luna sessions, 2.2 over 19 gpt-6.2-sol sessions) at about 1,600 prompt tokens, none
# cached, and 220 output tokens a reply.
SIMULATOR_REQUESTS = 2 / 12
SIMULATOR_PROMPT = 1_600
SIMULATOR_OUTPUT = 220


class Profile(BaseModel):
    """Average tokens per shift and where they came from."""

    prompt: float
    cached: float
    cache_write: float
    completion: float
    largest_prompt: int
    source: str

    def session(self, shifts: int) -> Usage:
        return Usage(
            prompt_tokens=round(self.prompt * shifts),
            cached_tokens=round(self.cached * shifts),
            cache_write_tokens=round(self.cache_write * shifts),
            completion_tokens=round(self.completion * shifts),
            largest_prompt_tokens=self.largest_prompt,
        )


def average(usages: list[tuple[Usage, int]], source: str) -> Profile:
    shifts = sum(count for _, count in usages)
    return Profile(
        prompt=sum(u.prompt_tokens for u, _ in usages) / shifts,
        cached=sum(u.cached_tokens for u, _ in usages) / shifts,
        cache_write=sum(u.cache_write_tokens for u, _ in usages) / shifts,
        completion=sum(u.completion_tokens for u, _ in usages) / shifts,
        largest_prompt=max(u.largest_prompt_tokens for u, _ in usages),
        source=source,
    )


def model_profile(name: str, stored: dict[str, list[SessionRun]]) -> Profile | None:
    """The model's own average, else the mean of the other stored models' averages."""
    own = stored.get(name) or []
    if own:
        return average([(r.usage, r.setting.shifts) for r in own], f"{len(own)} own sessions")
    others = [
        average([(r.usage, r.setting.shifts) for r in runs], model)
        for model, runs in sorted(stored.items())
        if runs
    ]
    if not others:
        return None
    return Profile(
        prompt=sum(p.prompt for p in others) / len(others),
        cached=sum(p.cached for p in others) / len(others),
        cache_write=sum(p.cache_write for p in others) / len(others),
        completion=sum(p.completion for p in others) / len(others),
        largest_prompt=max(p.largest_prompt for p in others),
        source=f"average of {', '.join(p.source for p in others)}",
    )


def simulator_profile(name: str, stored: dict[str, list[SessionRun]]) -> Profile:
    counted = [
        (r.simulator_usage, r.setting.shifts)
        for runs in stored.values()
        for r in runs
        if r.simulator == name and r.simulator_usage is not None
    ]
    if counted:
        return average(counted, f"{len(counted)} stored sessions")
    return Profile(
        prompt=SIMULATOR_REQUESTS * SIMULATOR_PROMPT,
        cached=0.0,
        cache_write=0.0,
        completion=SIMULATOR_REQUESTS * SIMULATOR_OUTPUT,
        largest_prompt=SIMULATOR_PROMPT,
        source="assumed",
    )


def session_usd(pricing: Pricing, profile: Profile, shifts: int) -> float:
    return pricing.cost(profile.session(shifts))
