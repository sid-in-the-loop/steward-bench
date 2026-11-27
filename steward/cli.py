"""The command line: steward check, run, probe, score and estimate."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from steward.config import Settings, load_settings

if TYPE_CHECKING:
    from steward.models import Model
    from steward.runner import SessionCase
    from steward.simulator import CachedSimulator

log = logging.getLogger("steward")


def setup_logging(settings: Settings) -> None:
    settings.paths.logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(settings.paths.logs / "steward.log"),
        ],
    )


def cmd_lint_tasks(settings: Settings, args: argparse.Namespace) -> int:
    from steward.task import cases_root
    from steward.task_lint import lint

    findings = lint(cases_root(settings))
    for finding in findings:
        print(finding)
    print(f"{len(findings)} findings")
    return 0


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    from steward.check import build_session, check_benchmark
    from steward.runner import load_sessions

    failed = 0
    for case, spec in load_sessions(settings, args.case):
        if not args.check_only:
            build_session(settings, case, spec)
        warnings: list[str] = []
        problems = check_benchmark(settings, case, spec, args.one_date, args.workers, warnings)
        failed += bool(problems)
        print(f"{case.id}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"  {problem}")
        for warning in warnings:
            print(f"  warning: {warning}")
    return 1 if failed else 0


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    from steward.runner import load_sessions

    return run_benchmark(settings, args, load_sessions(settings, args.case))


# The value of --simulator-model that runs sessions with nobody played by a model.
NO_SIMULATOR = "none"
# What a benchmark run may spend without --max-usd, the tested models and the simulator
# together.
BENCHMARK_MAX_USD = 25.0


def runnable(names: list[str]) -> list[Model] | None:
    """The models to run, or None after saying why one of them cannot be run."""
    from steward.models import endpoint

    try:
        return [endpoint(name) for name in names]
    except KeyError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return None


def run_benchmark(settings: Settings, args: argparse.Namespace, cases: list[SessionCase]) -> int:
    """The benchmark run: the seeded two-day schedule under each condition asked for. The
    estimate covers the tested model's sessions not yet stored; the limit covers the tested
    models and the simulator together, and what the simulator spent is printed at the end."""
    from steward.check import BENCHMARK_TRIALS
    from steward.estimate import model_profile, session_usd, simulator_profile
    from steward.models import MODELS
    from steward.runner import (
        Budget,
        Job,
        benchmark_jobs,
        estimate_usd,
        pending,
        run_jobs,
        sessions_path,
    )
    from steward.schedule import SHORT_FORMS

    trials = 1 if args.trials is None else args.trials
    if not 1 <= trials <= len(BENCHMARK_TRIALS):
        print(
            f"Refused: validation replays trials {', '.join(map(str, BENCHMARK_TRIALS))} only, "
            f"so the benchmark runs 1 to {len(BENCHMARK_TRIALS)} trials, not {trials}",
            file=sys.stderr,
        )
        return 2
    conditions = [SHORT_FORMS[name] for name in dict.fromkeys(args.condition)]
    jobs = benchmark_jobs(cases, conditions, trials)
    models = runnable(args.model)
    played_by = None if args.simulator_model == NO_SIMULATOR else args.simulator_model
    playing = runnable([played_by]) if played_by else []
    if models is None or playing is None:
        return 2
    limit = BENCHMARK_MAX_USD if args.max_usd is None else args.max_usd
    stored = stored_by_model(settings)
    played = [(sim, simulator_profile(sim.name, stored)) for sim in playing]

    def tested_usd(model: Model, job: Job) -> tuple[float, str]:
        profile = model_profile(model.name, stored)
        if profile is None:
            return estimate_usd(model, job.setting), "assumed"
        return session_usd(model.pricing, profile, job.setting.items), profile.source

    def expected(model: Model) -> Callable[[Job], float]:
        def usd(job: Job) -> float:
            tested = tested_usd(model, job)[0]
            return tested + sum(session_usd(s.pricing, p, job.setting.items) for s, p in played)

        return usd

    for model in models:
        todo = pending(sessions_path(settings, model.name), jobs, played_by)
        dollars = sum(tested_usd(model, job)[0] for job in todo)
        source = tested_usd(model, jobs[0])[1] if jobs else "assumed"
        line = (
            f"{model.name}: {len(todo)} of {len(jobs)} sessions to run, about ${dollars:.2f} "
            f"for the tested model (tokens from {source})"
        )
        if model.reasoning is not None:
            line += f", reasoning at its provider's default ({model.reasoning})"
        for sim, profile in played:
            assumed = sum(session_usd(sim.pricing, profile, job.setting.items) for job in todo)
            line += (
                f" and about ${assumed:.2f} for the simulator {sim.name} "
                f"(tokens from {profile.source})"
            )
        print(line)
    print(f"limit ${limit:.2f} for the tested models and the simulator together")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    # Without a simulator people say only their fixed lines: enough to exercise the tasks.
    simulator = None if played_by is None else simulator_for(settings, played_by)

    def simulator_spent() -> float:
        if simulator is None or simulator.model not in MODELS:
            return 0.0
        return MODELS[simulator.model].pricing.cost(simulator.usage)

    budget = Budget(limit, others=simulator_spent)
    for model in models:
        run_jobs(
            settings,
            model,
            jobs,
            args.workers,
            simulator,
            budget,
            args.start,
            retry_failed=args.retry_failed,
            expected=expected(model),
        )
    if simulator is not None:
        used = simulator.usage
        cost = MODELS[simulator.model].pricing.cost(used) if simulator.model in MODELS else 0.0
        print(
            f"simulator {simulator.model}: {used.requests} requests, {used.prompt_tokens} "
            f"input tokens ({used.cached_tokens} cached), {used.completion_tokens} output "
            f"tokens, ${cost:.4f}"
        )
    print(f"tested models: ${budget.spent:.4f}")
    return 0


def cmd_probe(settings: Settings, args: argparse.Namespace) -> int:
    """Send the simulator four fixed probes per person who holds a fact, with no agent, and
    grade the replies by rule."""
    from steward.models import MODELS
    from steward.probe import estimate_usd, planned, probe_cases, probe_path, summary
    from steward.runner import load_sessions

    cases = [
        (case, spec)
        for case, spec in load_sessions(settings, args.case)
        if args.case or (spec.goal is not None and spec.conflict is not None)
    ]
    pricing = MODELS[args.simulator_model].pricing
    probes, unnamed = planned(cases)
    for task in unnamed:
        print(f"no name for the thing, P2 and P3 left out: {task}")
    dollars = estimate_usd(probes, pricing)
    print(f"{len(cases)} tasks, {probes} probes with {args.simulator_model}, about ${dollars:.2f}")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    results = probe_cases(
        settings,
        cases,
        lambda: simulator_for(settings, args.simulator_model),
        pricing,
        args.workers,
    )
    path = probe_path(settings, args.simulator_model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(r.model_dump_json() + "\n" for r in results))
    for line in summary(results):
        print(line)
    print(f"written to {path}")
    return 0


def simulator_for(settings: Settings, model: str) -> CachedSimulator:
    """The user simulator: Claude through the Anthropic API, or an OpenAI model through the
    Responses API, with the same instructions and the same reply cache layout."""
    import os

    import httpx

    from steward.models import MODELS
    from steward.rate_log import rate_log
    from steward.simulator import ClaudeSimulator, OpenAISimulator, simulator_cache

    if model.startswith("claude-"):
        import anthropic

        return ClaudeSimulator(anthropic.Anthropic(), simulator_cache(settings), model)
    endpoint = MODELS[model]
    client = httpx.Client(
        base_url=endpoint.base_url,
        headers={"Authorization": f"Bearer {os.environ.get(endpoint.api_key_env, '')}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
        event_hooks={"response": [rate_log(settings, model).hook("simulator")]},
    )
    return OpenAISimulator(client, simulator_cache(settings), model)


def stored_by_model(settings: Settings) -> dict[str, list[Any]]:
    """Every priced model's stored sessions."""
    from steward.prices import PRICES
    from steward.runner import sessions_path, stored_sessions

    return {name: stored_sessions(sessions_path(settings, name)) for name in PRICES}


def cmd_estimate(settings: Settings, args: argparse.Namespace) -> int:
    """The cost of the whole planned benchmark for each model, with nothing run."""
    from steward.estimate import model_profile, session_usd, simulator_profile
    from steward.prices import pricing_of
    from steward.runner import benchmark_jobs, load_sessions
    from steward.schedule import SHORT_FORMS

    only = None
    if args.cases is not None:
        only = [line.strip() for line in args.cases.read_text().splitlines() if line.strip()]
    try:
        prices = {name: pricing_of(name) for name in dict.fromkeys(args.model)}
        played = None if args.simulator_model == NO_SIMULATOR else args.simulator_model
        simulator = pricing_of(played) if played else None
    except KeyError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    conditions = [SHORT_FORMS[name] for name in dict.fromkeys(args.condition or ["sd", "sid"])]
    jobs = benchmark_jobs(load_sessions(settings, only), conditions, args.trials)
    stored = stored_by_model(settings)
    played_profile = simulator_profile(played, stored) if played else None
    print(f"{len(jobs)} sessions per model, {args.trials} trial(s), {', '.join(conditions)}")
    total = 0.0
    for name, pricing in prices.items():
        profile = model_profile(name, stored)
        if profile is None:
            print(f"{name}: no stored sessions of any model to estimate from")
            continue
        dollars = sum(session_usd(pricing, profile, job.setting.items) for job in jobs)
        total += dollars
        print(f"{name:20} ${dollars:9.2f}  tokens from {profile.source}")
    if simulator is not None and played_profile is not None:
        each = sum(session_usd(simulator, played_profile, job.setting.items) for job in jobs)
        dollars = each * len(prices)
        total += dollars
        print(
            f"{'simulator ' + str(played):20} ${dollars:9.2f}  ${each:.2f} per model, "
            f"tokens from {played_profile.source}"
        )
    print(f"{'total':20} ${total:9.2f}")
    return 0


def cmd_score(settings: Settings, args: argparse.Namespace) -> int:
    """Print each model's metrics per condition (docs/framework.md), and for two or more
    models the difference of every pair's Steward scores with its interval and its p-value
    corrected across the pairs."""
    from itertools import combinations

    from steward.runner import current_sessions, load_sessions
    from steward.schedule import CONDITIONS
    from steward.score import (
        difference,
        holm,
        latest,
        lines,
        outside_conditions,
        score,
    )

    specs = {case.id: spec for case, spec in load_sessions(settings)}
    simulator = None if args.simulator_model == NO_SIMULATOR else args.simulator_model
    runs = {}
    for model in args.model:
        found, duplicates = latest(
            [
                run
                for run in current_sessions(settings, [model])
                if (not args.trial or run.trial in args.trial) and run.simulator == simulator
            ]
        )
        runs[model] = found
        outside = outside_conditions(found, specs)
        if duplicates or outside:
            print(
                f"{model}: {duplicates} earlier duplicate sessions dropped, "
                f"{outside} sessions on no benchmark schedule left out"
            )
    for condition in CONDITIONS:
        for model in args.model:
            scored = score(model, runs[model], condition, specs)
            for line in lines(scored) if scored else [f"{model}, {condition}: no sessions"]:
                print(line)
        pairs = [
            (first, second, gap)
            for first, second in combinations(args.model, 2)
            if (gap := difference(runs[first], runs[second], condition, specs)) is not None
        ]
        corrected = holm([gap for _, _, gap in pairs])
        for (first, second, _), gap in zip(pairs, corrected, strict=True):
            low, high = gap.interval
            verdict = "significant" if gap.significant else "not distinguishable"
            print(
                f"{first} minus {second}, {condition}: {gap.difference:+.3f} "
                f"({low:+.3f} to {high:+.3f}) on {gap.tasks} tasks both ran, p {gap.p_value:.4f}"
                f" (Holm over {len(pairs)} pairs {gap.adjusted:.4f}), {verdict}; left out "
                f"{gap.first_only} sessions only {first} ran and {gap.second_only} only "
                f"{second} ran"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward", description="Steward Bench")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str, handler: Callable[[Settings, argparse.Namespace], int], help_text: str
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    sub = add("check", cmd_check, "build each task and replay its references on the schedule")
    sub.add_argument("--case", action="append", help="a task ID; repeat for several")
    sub.add_argument("--check-only", action="store_true", help="do not rebuild, only check")
    sub.add_argument(
        "--one-date", action="store_true", help="show every shift the session's first date"
    )
    sub.add_argument("--workers", type=int, default=1, help="replays run at once per task")
    sub = add("run", cmd_run, "run models on the benchmark sessions not yet stored")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--case", action="append", help="a task ID; repeat for several")
    sub.add_argument(
        "--condition",
        action="append",
        choices=["sd", "sid"],
        required=True,
        help="sd is State Drift, sid is State and Instruction Drift",
    )
    sub.add_argument(
        "--trials",
        type=int,
        help="trials of each session: 1 by default, and at most the trials validation replays (2)",
    )
    sub.add_argument("--workers", type=int, default=2)
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub.add_argument(
        "--simulator-model",
        default="claude-sonnet-5-5",
        help="who plays the people; 'none' leaves them their fixed lines",
    )
    sub.add_argument(
        "--start",
        type=int,
        help="sessions at once to begin with; more are added up to --workers while the "
        "provider answers without rate limits",
    )
    sub.add_argument(
        "--max-usd",
        type=float,
        help="start no session once the tested models and the simulator have spent this much "
        f"(default {BENCHMARK_MAX_USD:.0f})",
    )
    sub.add_argument(
        "--retry-failed",
        action="store_true",
        help="try again sessions that failed 4 times by the tested model's own error",
    )
    sub = add("probe", cmd_probe, "send the user simulator fixed probes and grade them")
    sub.add_argument(
        "--case",
        action="append",
        help="a task ID; repeat for several (default: every benchmark task)",
    )
    sub.add_argument("--simulator-model", default="claude-sonnet-5-5")
    sub.add_argument("--workers", type=int, default=8, help="tasks probed at once")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("score", cmd_score, "print the benchmark's metrics per model and condition")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--trial", action="append", type=int, help="keep sessions of this trial")
    sub.add_argument(
        "--simulator-model",
        required=True,
        help="score the sessions whose people this model played; 'none' for fixed lines",
    )
    sub = add("estimate", cmd_estimate, "estimate the whole benchmark's cost, run nothing")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--trials", type=int, default=1)
    sub.add_argument("--cases", type=Path, help="a file of task IDs, one a line (default: all)")
    sub.add_argument("--condition", action="append", choices=["sd", "sid"], help="default both")
    sub.add_argument("--simulator-model", default="claude-sonnet-5-5", help="or 'none'")
    add("lint-tasks", cmd_lint_tasks, "list repeated names, shared sentences and loose patterns")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    settings = load_settings()
    args = build_parser().parse_args(argv)
    setup_logging(settings)
    code: int = args.handler(settings, args)
    return code


if __name__ == "__main__":
    sys.exit(main())
