"""Build cases, check them against their reference runs, and run models on them."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from steward.cases.case import Case, case_directories, load_case
from steward.cases.prices import pricing_of
from steward.cases.run import Model
from steward.config import Settings

# The models with an endpoint set up here, at their prices in PRICES. claude-sonnet-5-5 plays
# people in session runs and is not run as an agent from this machine.
MODELS = {
    name: Model(name=name, pricing=pricing_of(name))
    for name in ["gpt-6-luna", "gpt-5.6-luna", "gpt-5-mini", "gpt-6.2-sol", "gpt-6.1-terra"]
} | {
    name: Model(
        name=name, dialect="anthropic", api_key_env="ANTHROPIC_API_KEY", pricing=pricing_of(name)
    )
    for name in ["claude-haiku-5-5", "claude-sonnet-5-5"]
}


class UnknownModelError(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


def endpoint(name: str) -> Model:
    """The model to run, or a refusal that says why it cannot be run."""
    pricing_of(name)
    if name not in MODELS:
        raise UnknownModelError(
            f"{name} has a price but no endpoint set up in MODELS in steward/cases/commands.py"
        )
    return MODELS[name]


def cases_root(settings: Settings) -> Path:
    return settings.paths.root / "cases"


def simulator_cache(settings: Settings) -> Path:
    return settings.paths.data / "cases" / "simulator_cache"


def load_cases(settings: Settings, only: list[str] | None = None) -> list[Case]:
    cases = [load_case(d) for d in case_directories(cases_root(settings), only)]
    return [c for c in cases if not only or c.id in only]


# With STEWARD_PROCESS_CEILING set, no run starts while this user's process count is above
# it: a login node that allows 1,000 processes and threads fails commands in confusing ways
# near that (600 suits it). Unset, there is no ceiling, as on a compute node, where a fixed
# one already exceeded would stall every run.
PROCESS_COUNT = Path("/sys/fs/cgroup/user.slice") / f"user-{os.getuid()}.slice" / "pids.current"
CEILING_VARIABLE = "STEWARD_PROCESS_CEILING"
PROCESS_WAIT_SECONDS = 10.0
# How long a run waits for the count to fall before it gives up.
PROCESS_WAIT_LIMIT_SECONDS = 1800.0


class NoRoomError(RuntimeError):
    """The process count stayed above the ceiling for the whole wait."""


def wait_for_room(
    sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print
) -> None:
    """Wait while this user's process count is above STEWARD_PROCESS_CEILING, saying so once,
    and give up with NoRoomError after PROCESS_WAIT_LIMIT_SECONDS."""
    ceiling = os.environ.get(CEILING_VARIABLE)
    if not ceiling or not PROCESS_COUNT.exists():
        return
    waited = 0.0
    while (count := int(PROCESS_COUNT.read_text())) > int(ceiling):
        if waited >= PROCESS_WAIT_LIMIT_SECONDS:
            raise NoRoomError(
                f"{count} processes, above {CEILING_VARIABLE}={ceiling}, for {waited:.0f} s"
            )
        if not waited:
            log(f"waiting: {count} processes, above {CEILING_VARIABLE}={ceiling}")
        sleep(PROCESS_WAIT_SECONDS)
        waited += PROCESS_WAIT_SECONDS
