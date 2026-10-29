"""Every source in brief section 4, with its loader or the reason it is unavailable."""

from __future__ import annotations

from pydantic import BaseModel

from steward.sources.base import Source
from steward.sources.dataclaw import DataClaw
from steward.sources.hyperswitch import Hyperswitch
from steward.sources.nebius_openhands import NebiusOpenHands
from steward.sources.pi_staging import PiStaging
from steward.sources.swe_chat import SweChat
from steward.sources.swe_together import SweTogether
from steward.sources.swe_together_kimi import SweTogetherKimi


class UnavailableSource(BaseModel):
    name: str
    domain: str
    location: str
    license: str | None
    reason: str
    unblock: str


LOADERS: dict[str, Source] = {
    source.info.name: source
    for source in [
        SweChat(),
        Hyperswitch(),
        NebiusOpenHands(),
        SweTogether(),
        SweTogetherKimi(),
        PiStaging(),
        DataClaw(),
    ]
}

UNAVAILABLE: dict[str, UnavailableSource] = {
    entry.name: entry
    for entry in [
        UnavailableSource(
            name="spider2_dbt",
            domain="data",
            location="github:xlang-ai/Spider2",
            license="MIT",
            reason="68 local DuckDB tasks with no released agent trajectories",
            unblock="Generate trajectories in Phase 2 by running agents on the tasks",
        ),
    ]
}


def get(name: str) -> Source:
    if name not in LOADERS:
        raise KeyError(f"unknown source {name!r}; known: {sorted(LOADERS)}")
    return LOADERS[name]
