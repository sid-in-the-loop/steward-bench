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
        UnavailableSource(
            name="toolathlon",
            domain="tool_use",
            location="hf:hkust-nlp/Toolathlon-Trajectories, hf:hkust-nlp/Toolathlon-Verified_Trajectories",
            license="cc-by-4.0 (Trajectories); no tag (Verified)",
            reason="Gated: access forms for both trajectory datasets not accepted; the task "
            "dataset hkust-nlp/Toolathlon has no trajectories",
            unblock="Accept both access forms, then run data/logs/toolathlon_scripts/"
            "dl_toolathlon_lfs.py (1.78 GB subset); the Verified terms forbid training use and "
            "redistribution",
        ),
    ]
}


def get(name: str) -> Source:
    if name not in LOADERS:
        raise KeyError(f"unknown source {name!r}; known: {sorted(LOADERS)}")
    return LOADERS[name]
