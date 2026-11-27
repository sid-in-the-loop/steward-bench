"""Build cases, check them against their reference runs, and run models on them."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from steward.cases import sandbox
from steward.cases.case import Case, Repo, case_directories, load_case
from steward.cases.conditions import CONDITIONS, Condition, ConditionId, Timing
from steward.cases.prices import pricing_of
from steward.cases.run import Model, Run, Variant, built_directory, model_policy, run_case
from steward.cases.sandbox import workspace_image
from steward.cases.simulator import ClaudeSimulator
from steward.config import Settings
from steward.execution.policy import Message, Policy, ScriptedPolicy

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


# What one run is assumed to use when estimating cost: tokens of fresh input, cached input
# and output.
ASSUMED_TOKENS = (60_000, 400_000, 12_000)


def cases_root(settings: Settings) -> Path:
    return settings.paths.root / "cases"


def simulator_cache(settings: Settings) -> Path:
    return settings.paths.data / "cases" / "simulator_cache"


def runs_path(settings: Settings, model: str) -> Path:
    return settings.paths.data / "cases" / "runs" / f"{model}.jsonl"


def load_cases(settings: Settings, only: list[str] | None = None) -> list[Case]:
    cases = [load_case(d) for d in case_directories(cases_root(settings), only)]
    return [c for c in cases if not only or c.id in only]


def build_case(settings: Settings, case: Case) -> None:
    if not isinstance(case.environment, Repo):
        return
    base = (cases_root(settings) / "_bases" / case.environment.base).read_text()
    sandbox.build(case, base, built_directory(settings, case), workspace_image(settings))


def check_case(settings: Settings, case: Case) -> list[str]:
    """Run the two references. The safe one must leave no harm and disclose; the harmful one
    must leave harm. Returns what is wrong, if anything."""
    problems: list[str] = []
    condition = CONDITIONS["A1"]
    references = [("safe", case.safe_reference, False)]
    if case.harmful_reference is not None:
        references.append(("harmful", case.harmful_reference, True))
    for name, reference, harm in references:
        policy = ScriptedPolicy(list(reference.actions), reference.report)

        def scripted(_tools: list[Message], policy: ScriptedPolicy = policy) -> Policy:
            return policy

        run = run_case(settings, case, condition, scripted, name)
        outcome = run.outcome
        if outcome.harm != harm:
            problems.append(f"{name} reference: harm is {outcome.harm}, expected {harm}")
        if outcome.beyond_authority != harm:
            problems.append(f"{name} reference: beyond authority is {outcome.beyond_authority}")
        # A harmless twin has nothing to disclose.
        twin = case.harmful_reference is None
        if name == "safe" and outcome.done is False:
            problems.append("safe reference: it does not achieve what the requester asked for")
        if name == "safe" and not twin and outcome.disclosed is not True:
            problems.append("safe reference: its report does not match the disclose pattern")
        for event in run.events:
            if event.kind == "result" and event.text.strip() in ("", "No results."):
                problems.append(f"{name} reference: an action got no answer at turn {event.turn}")
    return problems


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


def estimate_usd(model: Model, runs: int) -> float:
    fresh, cached, output = ASSUMED_TOKENS
    price = model.pricing
    per_run = (
        fresh * price.prompt + cached * (price.cached or price.prompt) + output * price.completion
    )
    return runs * per_run / 1_000_000


def whole(line: str) -> bool:
    """Whether a line of a run file is a whole record."""
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def drop_faulty_runs(path: Path) -> int:
    """Remove lines cut short by a run that was stopped while writing, so they are run again.
    Returns how many were removed. A run in which the machine faulted is never stored (see
    `drop_faulty_sessions`), so a stored run's text is not searched for one."""
    if not path.exists():
        return 0
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    kept = [line for line in lines if whole(line)]
    if len(kept) != len(lines):
        path.write_text("".join(line + "\n" for line in kept))
    return len(lines) - len(kept)


def unversioned(path: Path) -> dict[str, str]:
    """Runs stored before cases had versions carry none. `unversioned.json`, beside the run
    files, names the cases whose early runs are still good and the version they count as."""
    listed = path.parent / "unversioned.json"
    return json.loads(listed.read_text()) if listed.exists() else {}


def stored_runs(path: Path) -> list[dict[str, Any]]:
    """Every stored run, each with the version of the case it counts for."""
    if not path.exists():
        return []
    early = unversioned(path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for row in rows:
        row["case_version"] = row.get("case_version") or early.get(row["case"], "")
    return rows


def done_keys(path: Path) -> set[tuple[str, str, str, int, str, str | None, str]]:
    return {
        (
            r["case"],
            r["case_version"],
            r["condition"],
            r["trial"],
            r.get("timing", "asked"),
            r.get("simulator"),
            r.get("variant", ""),
        )
        for r in stored_runs(path)
    }


def current_runs(settings: Settings, model: str, cases: list[Case]) -> list[dict[str, Any]]:
    """The stored runs of a model made on the present version of each case, one per case,
    condition, trial and setting."""
    versions = {case.id: case.version for case in cases}
    kept: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in stored_runs(runs_path(settings, model)):
        if versions.get(r["case"]) == r["case_version"]:
            key = (
                r["case"],
                r["condition"],
                r["trial"],
                r.get("timing", "asked"),
                r.get("simulator"),
                r.get("variant", ""),
            )
            kept.setdefault(key, r)
    return list(kept.values())


def run_model(
    settings: Settings,
    model: Model,
    cases: list[Case],
    conditions: list[ConditionId],
    trials: int,
    workers: int,
    timing: Timing = "asked",
    simulator: ClaudeSimulator | None = None,
    variant: Variant = "",
) -> list[Run]:
    """Every case, condition and trial not already in the model's run file. Each finished run
    is appended at once, so an interrupted command resumes where it stopped."""
    path = runs_path(settings, model.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    dropped = drop_faulty_runs(path)
    if dropped:
        print(f"{model.name}: {dropped} runs hit a machine fault and will be run again")
    done = done_keys(path)
    todo = [
        (case, CONDITIONS[c], trial)
        for case in cases
        for c in conditions
        for trial in range(trials)
        if (
            case.id,
            case.version,
            c,
            trial,
            timing,
            simulator.model if simulator else None,
            variant,
        )
        not in done
        and (timing == "asked" or CONDITIONS[c].informs is not None)
    ]
    lock = threading.Lock()
    key = os.environ.get(model.api_key_env, "")
    client = httpx.Client(
        base_url=model.base_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
    )

    def work(item: tuple[Case, Condition, int]) -> Run | None:
        case, condition, trial = item
        try:
            wait_for_room()
            run = run_case(
                settings,
                case,
                condition,
                model_policy(client, model, case),
                model.name,
                trial,
                model.pricing,
                timing,
                simulator,
                variant,
            )
        except Exception as error:  # noqa: BLE001 - one failed run must not stop the rest
            print(f"failed {case.id} {condition.id} trial {trial}: {type(error).__name__}: {error}")
            return None
        with lock, path.open("a") as out:
            out.write(run.model_dump_json() + "\n")
        o = run.outcome
        print(
            f"{model.name} {case.id} {condition.id} {timing} {variant or 'plain'} t{trial}: {run.ending}, looked={o.looked_first} "
            f"asked={o.asked} beyond={o.beyond_authority} harm={o.harm} disclosed={o.disclosed} "
            f"${run.cost_usd:.4f}",
            flush=True,
        )
        return run

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [r for r in pool.map(work, todo) if r is not None]
