"""The sources that feed the three benchmark domains, by name."""

from __future__ import annotations

from steward.sources.base import Source
from steward.sources.dataclaw import DataClaw
from steward.sources.openagentsafety import OpenAgentSafety
from steward.sources.swe_chat import SweChat
from steward.sources.swe_together import SweTogether
from steward.sources.terminal_bench import TerminalBench
from steward.sources.the_agent_company import TheAgentCompany
from steward.sources.toolathlon import Toolathlon

LOADERS: dict[str, Source] = {
    source.info.name: source
    for source in [
        SweChat(),
        SweTogether(),
        DataClaw(),
        TheAgentCompany(),
        OpenAgentSafety(),
        Toolathlon(),
        TerminalBench(),
    ]
}


def get(name: str) -> Source:
    if name not in LOADERS:
        raise KeyError(f"unknown source {name!r}; known: {sorted(LOADERS)}")
    return LOADERS[name]
