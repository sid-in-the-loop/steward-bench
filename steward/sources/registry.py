"""Every source in brief section 4, with its loader or the reason it is unavailable."""

from __future__ import annotations

from pydantic import BaseModel

from steward.sources.base import Source
from steward.sources.dataclaw import DataClaw
from steward.sources.hal_logs import HalLogs
from steward.sources.hyperswitch import Hyperswitch
from steward.sources.mle_traj import MleTraj
from steward.sources.nebius_openhands import NebiusOpenHands
from steward.sources.openagentsafety import OpenAgentSafety
from steward.sources.pi_staging import PiStaging
from steward.sources.swe_bench_experiments import SweBenchExperiments
from steward.sources.swe_chat import SweChat
from steward.sources.swe_together import SweTogether
from steward.sources.swe_together_kimi import SweTogetherKimi
from steward.sources.terminal_bench import TerminalBench
from steward.sources.the_agent_company import TheAgentCompany
from steward.sources.toolathlon import Toolathlon


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
        TheAgentCompany(),
        OpenAgentSafety(),
        HalLogs(),
        SweBenchExperiments(),
        Toolathlon(),
        TerminalBench(),
        MleTraj(),
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
        UnavailableSource(
            name="metr_malt",
            domain="ai_rnd",
            location="hf:metr-evals/malt-transcripts-public",
            license="MIT",
            reason="Gated: the HF account has not accepted the access form, every data file "
            "returns 403",
            unblock="Accept the form on the dataset page (approval is automatic), then download "
            "the 1.66 GB transcript set and write the graph-form loader",
        ),
    ]
}


def get(name: str) -> Source:
    if name not in LOADERS:
        raise KeyError(f"unknown source {name!r}; known: {sorted(LOADERS)}")
    return LOADERS[name]
