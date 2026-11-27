"""Single entry point: steward <command>."""

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
    from steward.cases.run import Model
    from steward.cases.session_commands import SessionCase
    from steward.cases.simulator import CachedSimulator

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
    from steward.cases.commands import cases_root
    from steward.cases.task_lint import lint

    findings = lint(cases_root(settings))
    for finding in findings:
        print(finding)
    print(f"{len(findings)} findings")
    return 0


def cmd_session_build(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import load_recordings
    from steward.cases.commands import cases_root
    from steward.cases.session_commands import (
        build_session,
        check_benchmark,
        check_changes,
        check_levels,
        check_session,
        idle_passes,
        load_sessions,
    )

    failed = 0
    for case, spec in load_sessions(settings, args.case):
        if not args.check_only:
            build_session(settings, case, spec)
        warnings: list[str] = []
        if args.benchmark:
            problems = check_benchmark(settings, case, spec, args.one_date, args.workers, warnings)
        else:
            problems = check_session(settings, case, spec, warnings)
            recordings = load_recordings(cases_root(settings) / case.id)
            problems += check_levels(settings, case, spec, recordings)
            problems += check_changes(settings, case, spec)
        failed += bool(problems)
        print(f"{case.id}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"  {problem}")
        for warning in warnings:
            print(f"  warning: {warning}")
        if args.idle and not args.benchmark:
            for warning in idle_passes(settings, case, spec):
                print(f"  warning: {warning}")
    return 1 if failed else 0


def cmd_session_run(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.commands import simulator_cache
    from steward.cases.session_commands import estimate_usd, load_sessions, run_sessions
    from steward.cases.sessions import EXPERIMENTS, Setting

    cases = load_sessions(settings, args.case)
    if args.condition:
        return run_benchmark(settings, args, cases)
    trials = EXPERIMENT_TRIALS if args.trials is None else args.trials
    chosen: dict[str, Setting] = {}
    for name in args.experiment or list(EXPERIMENTS):
        for setting in EXPERIMENTS[name]:
            chosen[setting.id] = setting
    if args.items:
        chosen = {k: s for k, s in chosen.items() if s.items in args.items}
    models = runnable(args.model)
    if models is None:
        return 2
    for model in models:
        each = sum(estimate_usd(model, setting) for setting in chosen.values())
        count = len(cases) * len(chosen) * trials
        print(f"{model.name}: up to {count} sessions, about ${each * len(cases) * trials:.2f}")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    import anthropic

    from steward.cases.simulator import SESSION_MODEL, ClaudeSimulator

    simulator = ClaudeSimulator(anthropic.Anthropic(), simulator_cache(settings), SESSION_MODEL)
    for model in models:
        run_sessions(
            settings,
            model,
            cases,
            list(chosen.values()),
            trials,
            args.workers,
            simulator,
            args.retry_failed,
        )
    used = simulator.input_tokens, simulator.output_tokens
    print(f"simulator: {used[0]} input tokens, {used[1]} output tokens")
    return 0


# The value of --simulator-model that runs sessions with nobody played by a model.
NO_SIMULATOR = "none"
# Trials of each experiment setting when --trials is not given; the benchmark runs one.
EXPERIMENT_TRIALS = 3
# What a benchmark run may spend without --max-usd, the tested models and the simulator
# together.
BENCHMARK_MAX_USD = 25.0


def runnable(names: list[str]) -> list[Model] | None:
    """The models to run, or None after saying why one of them cannot be run."""
    from steward.cases.commands import endpoint

    try:
        return [endpoint(name) for name in names]
    except KeyError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return None


def run_benchmark(settings: Settings, args: argparse.Namespace, cases: list[SessionCase]) -> int:
    """The benchmark run: the seeded two-day schedule under each condition asked for. The
    estimate covers the tested model's sessions not yet stored; the limit covers the tested
    models and the simulator together, and what the simulator spent is printed at the end."""
    from steward.cases.commands import MODELS
    from steward.cases.estimate import model_profile, session_usd, simulator_profile
    from steward.cases.schedule import SHORT_FORMS
    from steward.cases.session_commands import (
        BENCHMARK_TRIALS,
        Budget,
        Job,
        benchmark_jobs,
        estimate_usd,
        pending,
        run_jobs,
        sessions_path,
    )

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
    simulator = None if played_by is None else bench_simulator(settings, played_by)

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


def cmd_sim_probe(settings: Settings, args: argparse.Namespace) -> int:
    """Send the simulator four fixed probes per person who holds a fact, with no agent, and
    grade the replies by rule."""
    from steward.cases.commands import MODELS
    from steward.cases.session_commands import load_sessions
    from steward.cases.sim_probe import estimate_usd, planned, probe_cases, probe_path, summary

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
        lambda: bench_simulator(settings, args.simulator_model),
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


def cmd_bank_build(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bank import ClaudeWriter, build_bank, estimate_usd
    from steward.cases.bench import bench_cases
    from steward.cases.commands import cases_root
    from steward.cases.levels import BANK_FILE, save_bank

    root = cases_root(settings)
    cases = [
        (case, spec)
        for case, spec in bench_cases(settings, args.case)
        if args.again or not (root / case.id / BANK_FILE).exists()
    ]
    print(f"{len(cases)} banks to write, about ${estimate_usd(len(cases)):.2f}")
    if not args.confirm or not cases:
        print("Add --confirm to write them." if cases else "Nothing to write.")
        return 0
    import anthropic

    writer = ClaudeWriter(anthropic.Anthropic())
    for case, spec in cases:
        bank, rejected = build_bank(case, spec, writer, args.count)
        save_bank(root / case.id, bank)
        kinds = sum(len(by_kind) for by_kind in bank.lines.values())
        wordings = sum(len(w) for by_kind in bank.lines.values() for w in by_kind.values())
        print(f"{case.id}: {wordings} wordings in {kinds} kinds, {rejected} turned away")
    print(f"spent ${writer.cost_usd:.4f}")
    return 0


def cmd_exchanges_build(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import bench_cases
    from steward.cases.commands import cases_root
    from steward.cases.exchanges import (
        ClaudeExchanges,
        build_pools,
        estimate_usd,
        load_pool,
        missing,
        pools_of,
    )

    root = cases_root(settings)
    pools = [load_pool(root, pool) for pool in pools_of(bench_cases(settings))]
    pools = [pool for pool in pools if not args.pool or pool.name in args.pool]
    if args.again:
        for pool in pools:
            pool.exchanges = []
    parts = sum(len(missing(pool)) for pool in pools)
    for pool in pools:
        people = ", ".join(pool.people.values())
        print(
            f"{pool.name}: {people}; {len(pool.cases)} cases, {len(missing(pool))} parts to write"
        )
    print(f"{parts} parts to write with {args.model}, about ${estimate_usd(parts):.2f}")
    if not args.confirm or not parts:
        print("Add --confirm to write them." if parts else "Nothing to write.")
        return 0
    import anthropic

    writer = ClaudeExchanges(anthropic.Anthropic(), args.model)
    build_pools(root, pools, writer, args.workers)
    print(f"spent ${writer.cost_usd:.2f}")
    return 0


def cmd_owner_record(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import bench_cases, load_recordings
    from steward.cases.commands import cases_root
    from steward.cases.recorder import MANNERS, check_recording, record_owner, save_recording
    from steward.cases.simulator import MODEL

    root = cases_root(settings)
    cases = [(case, spec) for case, spec in bench_cases(settings, args.case) if spec.generated]
    wanted = {case.id: args.count - len(load_recordings(root / case.id)) for case, _ in cases}
    total = sum(max(0, missing) for missing in wanted.values())
    print(
        f"{total} recordings to make over {len(cases)} cases, about ${total * 0.02:.2f}, "
        f"or up to ${len(cases) * args.attempts * 0.02:.2f} if many are turned away"
    )
    if not args.confirm or not total:
        print("Add --confirm to record." if total else "Nothing to record.")
        return 0
    import anthropic

    from steward.execution.anthropic_policy import MAX_RETRIES, AnthropicPolicy

    client = anthropic.Anthropic(max_retries=MAX_RETRIES)
    short = []
    for case, spec in cases:
        attempts = 0
        while len(load_recordings(root / case.id)) < args.count and attempts < args.attempts:
            attempts += 1
            number = len(load_recordings(root / case.id))
            manner = MANNERS[(number + attempts - 1) % len(MANNERS)]
            commands = record_owner(
                settings, case, spec, lambda tools: AnthropicPolicy(client, MODEL, tools), manner
            )
            wrong = check_recording(settings, case, spec, commands) if commands else ["no commands"]
            if wrong:
                print(f"{case.id}: a recording was not a real drift and is not kept: {wrong}")
                continue
            path = save_recording(root / case.id, commands, MODEL, manner)
            print(f"{case.id}: {len(commands)} commands kept as {path.name}")
        if len(load_recordings(root / case.id)) < args.count:
            short.append(case.id)
    if short:
        print(f"fewer than {args.count} recordings after {args.attempts} attempts: {short}")
    return 0


def bench_simulator(settings: Settings, model: str) -> CachedSimulator:
    """The user simulator for bench-run: Claude through the Anthropic API, or an OpenAI model
    through the Responses API, with the same instructions and the same reply cache layout."""
    import os

    import httpx

    from steward.cases.commands import MODELS, simulator_cache
    from steward.cases.rate_log import rate_log
    from steward.cases.simulator import ClaudeSimulator, OpenAISimulator

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


def cmd_bench_run(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import (
        BENCH_MODELS,
        LEVELS,
        bench_cases,
        parse_counts,
        parse_workers,
        pilot_jobs,
        pilot_path,
        plan,
        run_bench,
        summarise,
        summarise_pilot,
    )
    from steward.cases.commands import MODELS

    models = [MODELS[name] for name in args.model or BENCH_MODELS]
    cases = bench_cases(settings, args.case)
    jobs, left_out = plan(settings, cases, args.level or list(LEVELS), parse_counts(args.settings))
    if args.pilot:
        jobs = pilot_jobs(jobs)
    if args.max_loops:
        jobs = [job for job in jobs if job.setting.items <= args.max_loops]
    if args.min_loops:
        jobs = [job for job in jobs if job.setting.items >= args.min_loops]
    for note in left_out:
        print(f"left out: {note}")
    total = 0.0
    for line in summarise(settings, models, jobs, args.simulator_model):
        total += line.usd
        print(
            f"{line.model:18} Level {line.level:3} {line.pending:5} of {line.planned:5} "
            f"sessions to run, about ${line.usd:.2f}"
        )
    print(f"{len(cases)} cases, about ${total:.2f} in all; limit ${args.max_usd:.2f}")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    if not args.pilot and not pilot_path(settings).exists():
        print("Run the pilot first: steward bench-run --pilot --confirm")
        return 1
    simulator = bench_simulator(settings, args.simulator_model)
    workers = parse_workers(args.workers, [m.name for m in models], 5)
    runs = run_bench(
        settings, models, jobs, workers, args.max_usd, simulator, args.start, args.minutes
    )
    print(f"simulator: {simulator.input_tokens} input tokens, {simulator.output_tokens} output")
    if args.pilot:
        summary = summarise_pilot(runs, {case.id: case.domain for case, _ in cases})
        pilot_path(settings).write_text(summary.model_dump_json(indent=2))
        print(summary.model_dump_json(indent=2))
    return 0


def cmd_bench_view(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench_view import collect, write_view

    records = collect(settings, args.model, args.case, args.level)
    page, files, sessions = write_view(records, args.out, args.title)
    beside = f" and {files} data files beside it" if files else ""
    print(f"{sessions} sessions written to {page}{beside}")
    return 0


def cmd_bench_digest(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.catalogue import catalogue
    from steward.cases.digest import write_digests

    sessions, loops = write_digests(catalogue(settings, args.model), args.out)
    print(f"{sessions} sessions and {loops} loops written to {args.out}")
    return 0


def cmd_bench_sample(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import BENCH_MODELS
    from steward.cases.catalogue import catalogue
    from steward.cases.validation_sample import Light, draw, key_of, outcome_of, write_sample

    models = args.model or list(BENCH_MODELS)
    analysis = settings.paths.data / "cases" / "analysis"
    out = args.out or analysis / "validation"
    digests = args.digests or analysis / "digests"
    lights = [
        Light(key=key_of(e.run), level=e.run.setting.level, outcome=outcome_of(e.run))
        for e in catalogue(settings, models)
        if e.status == "current"
    ]
    chosen = draw(lights, args.size, args.minimum, args.seed)
    wanted = set(chosen)
    entries = [
        e for e in catalogue(settings, models) if e.status == "current" and key_of(e.run) in wanted
    ]
    rows, verdicts = write_sample(entries, chosen, out, digests)
    print(
        f"{len(chosen)} of {len(lights)} sessions drawn; {rows} rows written to {out}, "
        f"{verdicts} with a verdict kept"
    )
    return 0


def cmd_bench_factors(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import BENCH_MODELS
    from steward.cases.catalogue import catalogue
    from steward.cases.factors import collect, write_factors

    models = args.model or list(BENCH_MODELS)
    out = args.out or settings.paths.data / "cases" / "analysis" / "factors"
    cells = collect(catalogue(settings, models))
    results = write_factors(cells, models, out, args.reps, args.seed)
    dials = sum(v.verdict == "raises harm" and v.factor != "level" for v in results.verdicts)
    print(
        f"{len(results.effects)} effects over {len(results.verdicts)} comparisons; "
        f"{dials} candidate dials; written to {out}"
    )
    return 0


def cmd_bench_check_gap(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import BENCH_MODELS
    from steward.cases.catalogue import catalogue
    from steward.cases.check_gap import collect_gaps, write_gaps

    models = args.model or list(BENCH_MODELS)
    out = args.out or settings.paths.data / "cases" / "analysis" / "check_gap"
    gaps = collect_gaps(catalogue(settings, models))
    results = write_gaps(gaps, models, out, args.reps, args.seed, args.min_recall)
    left_out = sum(not r.included for r in results.recall)
    print(
        f"{sum(gaps.loops.values())} scored drift loops; {left_out} of {len(results.recall)} "
        f"cases left out for a look pattern below {args.min_recall:.0%} recall; written to {out}"
    )
    return 0


def cmd_deck_data(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import BENCH_MODELS
    from steward.cases.deck_data import build_deck, read_measures, read_stale, write_deck

    models = args.model or list(BENCH_MODELS)
    analysis = settings.paths.data / "cases" / "analysis"
    measures = read_measures(args.measures or analysis / "digests" / "measures.csv")
    stale = read_stale(args.rates or analysis / "check_gap" / "rates.csv", models)
    out = args.out or settings.paths.root / "presentation" / "numbers" / "deck.js"
    write_deck(build_deck(measures, stale, models, args.reps, args.seed), out)
    print(f"numbers for {len(models)} models from {len(measures)} loops written to {out}")
    return 0


def cmd_bench_discrimination(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import BENCH_MODELS
    from steward.cases.catalogue import catalogue
    from steward.cases.discrimination import collect_loops, write_discrimination

    models = args.model or list(BENCH_MODELS)
    out = args.out or settings.paths.data / "cases" / "analysis" / "discrimination"
    loops = collect_loops(catalogue(settings, models))
    results = write_discrimination(loops, models, out, args.reps, args.seed)
    cautious = [c.model for c in results.caution if c.safe_by_caution]
    print(
        f"{len(results.rows)} comparisons; safe by caution: {', '.join(cautious) or 'none'}; "
        f"written to {out}"
    )
    return 0


def cmd_bench_set_aside(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.set_aside import set_aside_misrouted

    moved = set_aside_misrouted(settings, args.model)
    for model, count in moved.items():
        print(f"{model}: {count} sessions set aside")
    print(f"{sum(moved.values())} sessions set aside in all; the next bench-run makes them again")
    return 0


def stored_by_model(settings: Settings) -> dict[str, list[Any]]:
    """Every priced model's stored sessions."""
    from steward.cases.prices import PRICES
    from steward.cases.session_commands import sessions_path, stored_sessions

    return {name: stored_sessions(sessions_path(settings, name)) for name in PRICES}


def cmd_bench_estimate(settings: Settings, args: argparse.Namespace) -> int:
    """The cost of the whole planned benchmark for each model, with nothing run."""
    from steward.cases.estimate import model_profile, session_usd, simulator_profile
    from steward.cases.prices import pricing_of
    from steward.cases.schedule import SHORT_FORMS
    from steward.cases.session_commands import benchmark_jobs, load_sessions

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


def cmd_bench_spent(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import spend_limit_path, spent_usd

    spent = spent_usd(settings)
    path = spend_limit_path(settings)
    limit = float(path.read_text()) if path.exists() else None
    if args.room:
        print(f"{limit - spent:.2f}" if limit is not None else "none")
    else:
        print(f"${spent:.2f} spent on stored sessions" + (f" of ${limit:.2f}" if limit else ""))
    return 0


def cmd_bench_status(settings: Settings, args: argparse.Namespace) -> int:
    from collections import Counter

    from steward.cases.bench import BENCH_MODELS, LEVELS, bench_cases, plan, problems
    from steward.cases.case import DOMAIN_NAMES
    from steward.cases.session_commands import sessions_path, stored_key, stored_sessions

    cases = bench_cases(settings, args.case)
    jobs, _ = plan(settings, cases, list(LEVELS))
    wanted = {job.key: job for job in jobs}
    print(f"{'model':18} {'domain':20} level  planned  stored  complete")
    for name in args.model or BENCH_MODELS:
        stored: Counter[tuple[str, str]] = Counter()
        complete: Counter[tuple[str, str]] = Counter()
        seen = set()
        for run in stored_sessions(sessions_path(settings, name)):
            key = stored_key(run)
            if key not in wanted or key in seen:
                continue
            seen.add(key)
            cell = (wanted[key].case.domain, run.setting.level)
            stored[cell] += 1
            complete[cell] += not problems(run)
        planned = Counter((job.case.domain, job.setting.level) for job in jobs)
        for (domain, level), count in sorted(planned.items()):
            cell = (domain, level)
            print(
                f"{name:18} {DOMAIN_NAMES[domain]:20} {level:5} {count:8} "
                f"{stored[cell]:7} {complete[cell]:9}"
            )
    return 0


def cmd_session_table(settings: Settings, args: argparse.Namespace) -> int:
    import csv

    from steward.cases.commands import MODELS
    from steward.cases.session_commands import item_rows, sessions_path, stored_sessions

    runs = [r for name in MODELS for r in stored_sessions(sessions_path(settings, name))]
    rows = item_rows(runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ["case"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{args.out}: {len(rows)} work items from {len(runs)} sessions")
    return 0


def cmd_bench_score(settings: Settings, args: argparse.Namespace) -> int:
    """Print each model's metrics per condition (docs/framework.md), and for two or more
    models the difference of every pair's Steward scores with its interval and its p-value
    corrected across the pairs."""
    from itertools import combinations

    from steward.cases.schedule import CONDITIONS
    from steward.cases.score import (
        difference,
        holm,
        latest,
        lines,
        outside_conditions,
        score,
    )
    from steward.cases.session_commands import current_sessions, load_sessions

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


def cmd_session_report(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.commands import MODELS
    from steward.cases.session_commands import current_sessions, load_sessions
    from steward.cases.session_report import report_data, write_report

    models = list(MODELS)
    cases = [case.id for case, _ in load_sessions(settings)]
    data = report_data(current_sessions(settings, models), models, cases)
    print(write_report(data, args.out))
    print(f"{data['coverage']['sessions']} sessions, ${data['coverage']['cost_usd']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward", description="Steward Bench Phase 1 pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(
        name: str, handler: Callable[[Settings, argparse.Namespace], int], help_text: str
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    sub = add("bank-build", cmd_bank_build, "write the message bank of each benchmark case")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--count", type=int, default=5, help="wordings to ask for per kind")
    sub.add_argument("--again", action="store_true", help="rewrite banks that exist")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("exchanges-build", cmd_exchanges_build, "write the exchanges between users, by team")
    sub.add_argument("--pool", action="append", help="a pool name; repeat for several")
    sub.add_argument("--model", default="claude-sonnet-5-5")
    sub.add_argument("--workers", type=int, default=8, help="parts written at once")
    sub.add_argument("--again", action="store_true", help="rewrite exchanges that exist")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("owner-record", cmd_owner_record, "record a simulated owner making the change")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--count", type=int, default=3, help="recordings each case should have")
    sub.add_argument("--attempts", type=int, default=6, help="attempts per case at most")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("bench-run", cmd_bench_run, "run every case at every level under one limit")
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub.add_argument(
        "--simulator-model",
        default="claude-sonnet-5-5",
        help="the model that plays the users; an OpenAI model is called through its API with "
        "the same instructions (default claude-sonnet-5-5)",
    )
    sub.add_argument("--level", action="append", choices=["I", "II", "III", "IV", "V"])
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument(
        "--settings",
        action="append",
        help="settings per case at a drawn level, as LEVEL=N (III=20); repeat for several",
    )
    sub.add_argument(
        "--workers",
        action="append",
        help="most sessions at once per model, as N or MODEL=N; repeat for several (default 5)",
    )
    sub.add_argument(
        "--start",
        type=int,
        help="sessions at once to begin with; the number then follows the rate limits",
    )
    sub.add_argument(
        "--minutes", type=float, help="start no session that would not finish within this"
    )
    sub.add_argument(
        "--max-loops",
        type=int,
        help="leave out sessions longer than this, for a machine held too briefly for them",
    )
    sub.add_argument(
        "--min-loops", type=int, help="leave out sessions shorter than this, to run the long ones"
    )
    sub.add_argument("--max-usd", type=float, default=180.0, help="stop starting sessions here")
    sub.add_argument("--pilot", action="store_true", help="one setting per domain and level")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("bench-view", cmd_bench_view, "write the viewer of stored benchmark sessions")
    sub.add_argument("--out", type=Path, required=True, help="the folder to write the page to")
    sub.add_argument("--title", default="Steward Bench sessions")
    sub.add_argument("--model", action="append")
    sub.add_argument("--case", action="append")
    sub.add_argument("--level", action="append", choices=["I", "II", "III", "IV", "V"])
    sub = add("bench-digest", cmd_bench_digest, "write every stored session as text for reading")
    sub.add_argument("--out", type=Path, required=True, help="the folder to write the digests to")
    sub.add_argument("--model", action="append")
    sub = add(
        "bench-sample",
        cmd_bench_sample,
        "draw sessions by model, level and outcome for checking the scorer's labels by reading",
    )
    sub.add_argument("--out", type=Path, help="default: data/cases/analysis/validation")
    sub.add_argument("--digests", type=Path, help="default: data/cases/analysis/digests")
    sub.add_argument("--size", type=int, default=100, help="sessions to draw in all")
    sub.add_argument(
        "--minimum", type=int, default=1, help="sessions from every group that has any"
    )
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "bench-factors",
        cmd_bench_factors,
        "estimate how each setting factor moves harm, per model, with case-clustered intervals",
    )
    sub.add_argument("--out", type=Path, help="default: data/cases/analysis/factors")
    sub.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "bench-check-gap",
        cmd_bench_check_gap,
        "harm on drift loops by how long before acting the agent last looked at what drifted",
    )
    sub.add_argument("--out", type=Path, help="default: data/cases/analysis/check_gap")
    sub.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument(
        "--min-recall",
        type=float,
        default=0.8,
        help="leave out cases whose look pattern sees less than this share of looks",
    )
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "bench-discrimination",
        cmd_bench_discrimination,
        "flags raised on drift loops against loops without drift, per model and level",
    )
    sub.add_argument("--out", type=Path, help="default: data/cases/analysis/discrimination")
    sub.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "deck-data",
        cmd_deck_data,
        "the numbers the presentation page shows, from the measures table and stale-check rates",
    )
    sub.add_argument("--measures", type=Path, help="default: the digests' measures.csv")
    sub.add_argument("--rates", type=Path, help="default: the stale-check rates.csv")
    sub.add_argument("--out", type=Path, help="default: presentation/numbers/deck.js")
    sub.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "bench-set-aside",
        cmd_bench_set_aside,
        "move stored sessions with a question sent to the wrong person beside the session file",
    )
    sub.add_argument("--model", action="append")
    sub = add(
        "bench-estimate", cmd_bench_estimate, "estimate the whole benchmark's cost, run nothing"
    )
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--trials", type=int, default=1)
    sub.add_argument("--cases", type=Path, help="a file of task IDs, one a line (default: all)")
    sub.add_argument("--condition", action="append", choices=["sd", "sid"], help="default both")
    sub.add_argument("--simulator-model", default="claude-sonnet-5-5", help="or 'none'")
    sub = add("bench-spent", cmd_bench_spent, "what the stored sessions cost, against the limit")
    sub.add_argument("--room", action="store_true", help="print only what is left, or none")
    sub = add("bench-status", cmd_bench_status, "count planned, stored and complete sessions")
    sub.add_argument("--model", action="append")
    sub.add_argument("--case", action="append")
    add("lint-tasks", cmd_lint_tasks, "list repeated names, template sentences and loose patterns")
    sub = add("session-build", cmd_session_build, "build the session cases and check them")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--check-only", action="store_true", help="do not rebuild, only check")
    sub.add_argument(
        "--idle",
        action="store_true",
        help="also report the shifts an agent that does nothing would pass",
    )
    sub.add_argument(
        "--benchmark",
        action="store_true",
        help="check on the benchmark's seeded schedule, trials 0 and 1, with mixed agents under "
        "State Drift, in place of the short one",
    )
    sub.add_argument(
        "--one-date",
        action="store_true",
        help="with --benchmark, show every shift the session's first date",
    )
    sub.add_argument("--workers", type=int, default=1, help="replays run at once per case")
    sub = add("session-run", cmd_session_run, "run models on sessions of several work items")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--case", action="append")
    sub.add_argument(
        "--experiment",
        action="append",
        choices=["length", "users", "changed", "position", "notes", "speaking"],
        help="the setting that is varied; all six when left out",
    )
    sub.add_argument("--items", action="append", type=int, help="keep sessions of this length")
    sub.add_argument(
        "--condition",
        action="append",
        choices=["sd", "sid"],
        help="run the benchmark's seeded two-day schedule under this condition, not the "
        "experiments: sd is State Drift, sid is State and Instruction Drift",
    )
    sub.add_argument(
        "--trials",
        type=int,
        help="trials of each session: 3 for the experiments; with --condition 1, and at most "
        "the trials validation replays (2)",
    )
    sub.add_argument("--workers", type=int, default=2)
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub.add_argument(
        "--simulator-model",
        default="claude-sonnet-5-5",
        help="who plays the people with --condition; 'none' leaves them their fixed lines",
    )
    sub.add_argument(
        "--start",
        type=int,
        help="with --condition, sessions at once to begin with; more are added up to --workers "
        "while the provider answers without rate limits",
    )
    sub.add_argument(
        "--max-usd",
        type=float,
        help="with --condition, start no session once the tested models and the simulator "
        f"have spent this much (default {BENCHMARK_MAX_USD:.0f})",
    )
    sub.add_argument(
        "--retry-failed",
        action="store_true",
        help="try again sessions that failed 4 times by the tested model's own error",
    )
    sub = add("sim-probe", cmd_sim_probe, "send the user simulator fixed probes and grade them")
    sub.add_argument(
        "--case",
        action="append",
        help="a case id; repeat for several (default: every task with a goal and a conflict)",
    )
    sub.add_argument("--simulator-model", default="claude-sonnet-5-5")
    sub.add_argument("--workers", type=int, default=8, help="tasks probed at once")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("session-table", cmd_session_table, "write one row per work item per session")
    sub.add_argument("--out", type=Path, required=True)
    sub = add("bench-score", cmd_bench_score, "print the benchmark's metrics per condition")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--trial", action="append", type=int, help="keep sessions of this trial")
    sub.add_argument(
        "--simulator-model",
        required=True,
        help="score the sessions whose people this model played; 'none' for fixed lines",
    )
    sub = add("session-report", cmd_session_report, "write the page of session figures")
    sub.add_argument("--out", type=Path, required=True)
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
