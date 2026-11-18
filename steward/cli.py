"""Single entry point: steward <command>."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from steward.config import Settings, load_settings
from steward.filter.batch import CostGuardError

if TYPE_CHECKING:
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


def cmd_sources(settings: Settings, args: argparse.Namespace) -> int:
    from steward.sources.registry import LOADERS

    for name, source in sorted(LOADERS.items()):
        print(f"{name:28} {source.info.domain:16} {source.info.location}")
    return 0


def _source_names(args: argparse.Namespace) -> list[str]:
    from steward.sources.registry import LOADERS

    return sorted(LOADERS) if args.source == "all" else [args.source]


def cmd_download(settings: Settings, args: argparse.Namespace) -> int:
    from steward.sources.registry import get

    for name in _source_names(args):
        log.info("download %s", name)
        get(name).download(settings)
    return 0


def cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    from steward.sources.base import Unavailable
    from steward.sources.registry import get
    from steward.store import is_written, write_source

    for name in _source_names(args):
        if is_written(settings.paths.normalized, name) and not args.force:
            log.info("ingest %s: already written, skipped", name)
            continue
        try:
            count = write_source(settings.paths.normalized, name, get(name).load(settings))
        except Unavailable as error:
            log.warning("ingest %s: unavailable: %s", name, error)
            continue
        log.info("ingest %s: %d trajectories", name, count)
    return 0


def cmd_infer_commits(settings: Settings, args: argparse.Namespace) -> int:
    from steward.sources.commit_inference import infer_for_source

    infer_for_source(settings, args.source, settings.stage_a.min_agent_actions)
    return 0


def cmd_stage_a(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_a

    stage_a.run(settings, force=args.force)
    return 0


def cmd_stage_b(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_b

    inputs = stage_b.full_inputs(settings)
    if args.limit:
        inputs = inputs[: args.limit]
    stage_b.run(
        settings,
        inputs,
        settings.paths.stage_b / "results.jsonl",
        "stage_b",
        args.confirm,
        args.max_usd,
    )
    return 0


def cmd_stage_c1(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_b, stage_c1
    from steward.filter.stage_a import read_results

    b_results = stage_b.read_results(settings.paths.stage_b / "results.jsonl")
    stage_a = {r.trajectory_id: r for r in read_results(settings)}
    inputs = stage_c1.select_inputs(settings, b_results, stage_a)
    if args.limit:
        inputs = inputs[: args.limit]
    stage_c1.run(
        settings,
        inputs,
        settings.paths.stage_c1 / "results.jsonl",
        "stage_c1",
        args.confirm,
        args.max_usd,
    )
    return 0


def cmd_c2_handoff(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_c2

    paths = settings.paths
    shards = stage_c2.build_shards(
        settings,
        paths.stage_c1 / "results.jsonl",
        paths.stage_b / "results.jsonl",
        paths.handoff_c2,
    )
    for path in shards:
        print(path)
    return 0


def cmd_c2_merge(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_c2

    paths = settings.paths
    stage_c2.merge_results(paths.handoff_c2, paths.results_c2, paths.stage_c2 / "results.jsonl")
    return 0


RESULTS_BRANCH_PREFIX = "results/c2-shard-"


def cmd_c2_push(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.stage_c2 import push_handoff

    dirs = [settings.paths.handoff_c2]
    print(push_handoff(settings.paths.root, dirs, "Update Stage C2 handoff shards"))
    return 0


def cmd_c2_launch(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.stage_c2 import launch

    handoff = settings.paths.handoff_c2
    started = launch(
        settings.paths.root,
        handoff,
        settings.paths.results_c2,
        RESULTS_BRANCH_PREFIX,
        handoff.parent / f"{handoff.name}_launches.jsonl",
        min(args.max_running, settings.stage_c2.max_parallel_sessions),
        settings.paths.logs / "cloud",
    )
    for entry in started:
        print(entry.shard, entry.results_branch, entry.session_url)
    return 0


def cmd_c2_fetch(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.stage_c2 import fetch_results

    results = settings.paths.results_c2
    for path in fetch_results(settings.paths.root, RESULTS_BRANCH_PREFIX, results):
        print(path)
    return 0


def cmd_c2_split(settings: Settings, args: argparse.Namespace) -> int:

    from steward.filter import stage_c2

    for path in stage_c2.split_shard(Path(args.shard), Path(args.out_dir)):
        print(path)
    return 0


def cmd_c2_schema(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import stage_c2

    print(stage_c2.c2_json_schema())
    return 0


def cmd_rank(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import rank, stage_c2
    from steward.filter.stage_a import read_results
    from steward.store import read_meta, written_sources

    metas = {
        m.trajectory_id: m
        for source in written_sources(settings.paths.normalized)
        for m in read_meta(settings.paths.normalized, source)
    }
    actions = {r.trajectory_id: r.agent_actions for r in read_results(settings)}
    rank.run(settings, stage_c2.read_results(settings), actions, metas)
    return 0


def cmd_analyze(settings: Settings, args: argparse.Namespace) -> int:
    from steward.analysis.report import write_report

    write_report(settings)
    return 0


def cmd_audit_repositories(settings: Settings, args: argparse.Namespace) -> int:
    from steward.environment.audit import audit_repositories
    from steward.filter import stage_c2
    from steward.store import read_meta, written_sources

    metas = {
        m.trajectory_id: m
        for source in written_sources(settings.paths.normalized)
        for m in read_meta(settings.paths.normalized, source)
    }
    reports = audit_repositories(settings, stage_c2.read_results(settings), metas)
    built = [r for r in reports if r.checked_out]
    exact = [r for r in built if r.edits and r.edits_applied == r.edits]
    print(
        f"{len(reports)} sessions, {len(built)} checked out, {len(exact)} with every edit applied"
    )
    return 0


def cmd_author(settings: Settings, args: argparse.Namespace) -> int:
    """Run the authoring loop for one domain and one kind of environment."""
    from steward.environment.audit import audit_path
    from steward.environment.repository import ReplayReport
    from steward.execution.record import Action
    from steward.filter import stage_c2
    from steward.filter.selection import load_by_ids
    from steward.scenario import loop
    from steward.scenario.compile import gate_container, image_ready
    from steward.scenario.spec import ScenarioSpec
    from steward.schema import StageC2Result, Trajectory
    from steward.store import read_jsonl

    def recorded_tools(trajectory: Trajectory) -> dict[str, object]:
        return {"backend": "recorded_tools"}

    def repository(trajectory: Trajectory) -> dict[str, object]:
        return {
            "backend": "repository",
            "repository": trajectory.repo,
            "commit": trajectory.base_commit,
        }

    def task_image(trajectory: Trajectory) -> dict[str, object]:
        assert trajectory.task_env is not None
        task = trajectory.task_env.split(":", 1)[1].removeprefix("terminal-bench/")
        return {"backend": "task_image", "task_set": "terminal-bench", "task": task}

    def tool_servers(trajectory: Trajectory) -> dict[str, object]:
        task = str(trajectory.meta["task_name"])
        return {"backend": "tool_servers", "task_set": trajectory.source, "task": task}

    def container_gate(spec: ScenarioSpec, trajectory: Trajectory) -> list[str]:
        return gate_container(settings, spec, settings.paths.data / "phase2" / "gating")

    eligible = None
    extra_results: list[StageC2Result] = []
    latest_step: dict[str, int] = {}
    gate, compliant_actions, workers = loop.recorded_gate, None, 12
    if args.environment == "recorded_tools":
        # Workplace sessions act on hosted services; other sessions that cannot be rebuilt
        # exactly can also run on their recorded tool results.
        environment_of = recorded_tools
        sources = (
            {"toolathlon"}
            if args.domain == "workplace"
            else {"swe_chat", "dataclaw", "swe_together"}
        )
    elif args.environment == "tool_servers":
        # Sessions recorded on live workplace worlds; references run on the real tools.
        from functools import partial

        from steward.environment.gym_sessions import SOURCE, recorded_sessions
        from steward.scenario.worlds import gate_world, read_path
        from steward.store import write_source

        write_source(settings.paths.normalized, SOURCE, recorded_sessions(settings))
        sources, environment_of = {SOURCE}, tool_servers
        gate, workers = partial(gate_world, settings), 2
        extra_results = read_jsonl(read_path(settings), StageC2Result)
    elif args.environment == "task_image":
        # The task's own solution is the compliant reference; references run in the container.
        sources, environment_of = {"terminal_bench"}, task_image
        gate, workers = container_gate, 3
        compliant_actions = [Action(tool="Bash", arguments={"command": "bash /solution/solve.sh"})]
        eligible = {
            t.trajectory_id
            for t in load_by_ids(
                settings,
                [r.trajectory_id for r in stage_c2.read_results(settings) if r.source in sources],
            )
            if image_ready(settings, task_image(t))
        }
    else:
        # Only sessions whose working tree rebuilds exactly at the takeover step.
        sources, environment_of = {"swe_chat", "dataclaw", "swe_together"}, repository
        # A session with a mismatch is still exact before its first mismatching edit.
        reports = [r for r in read_jsonl(audit_path(settings), ReplayReport) if r.checked_out]
        eligible = {r.trajectory_id for r in reports}
        latest_step = {
            r.trajectory_id: r.first_mismatch_step
            for r in reports
            if r.first_mismatch_step is not None
        }
    if args.only:
        # For example the sessions whose environment was shown to rebuild faithfully.
        listed = set(Path(args.only).read_text().split())
        eligible = listed if eligible is None else eligible & listed
    outcomes = loop.author_domain(
        settings,
        args.domain,
        [*stage_c2.read_results(settings), *extra_results],
        sources,
        environment_of,
        gate,
        args.target,
        args.confirm,
        args.max_usd,
        eligible=eligible,
        latest_step=latest_step,
        compliant_actions=compliant_actions,
        workers=workers,
    )
    accepted = sum(o.accepted for o in outcomes)
    print(f"{args.domain}: {accepted} of {len(outcomes)} sessions accepted")
    return 0


def cmd_evaluate(settings: Settings, args: argparse.Namespace) -> int:
    """Run a model over the built scenarios under one experimental condition."""
    import json
    from pathlib import Path

    from steward.execution.evaluate import Condition, Endpoint, Pricing, evaluate, summarize

    condition = Condition(
        rule_position=args.rule_position,
        principal_authority=args.principal_authority,
        world=args.world,
        oversight=args.oversight,
        opening=args.opening,
        replies=args.replies,
        history_tokens=args.history_tokens,
        max_turns=args.max_turns,
        environment="live" if args.live else "recorded",
    )
    simulator = None
    if args.simulator_model:
        simulator = Endpoint(
            model=args.simulator_model,
            base_url=args.simulator_base_url,
            api_key_env=args.simulator_api_key_env,
        )
    endpoint = Endpoint(
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        provider=args.provider,
    )
    if args.openai:
        endpoint = Endpoint(
            model=args.model,
            base_url=args.base_url or "https://api.openai.com/v1",
            api_key_env=args.api_key_env or "OPENAI_API_KEY",
            dialect="openai",
        )
    pricing = Pricing(
        prompt=args.usd_per_mtok_prompt,
        completion=args.usd_per_mtok_completion,
        cached=args.usd_per_mtok_cached,
    )
    out = evaluate(
        settings,
        endpoint,
        condition,
        args.domain,
        args.limit,
        pricing,
        args.max_usd,
        args.confirm,
        planted={"planted": True, "recorded": False, "all": None}[args.scenarios],
        simulator=simulator,
        subset=args.subset,
        only=set(Path(args.only).read_text().split()) if args.only else None,
        workers=args.workers,
        resume=Path(args.resume) if args.resume else None,
    )
    print(out)
    print(json.dumps(summarize(out), indent=2))
    return 0


def cmd_plant(settings: Settings, args: argparse.Namespace) -> int:
    """Fit library designs to the sessions of a domain that already have a scenario."""
    from steward.filter import stage_c2
    from steward.scenario import loop

    outcomes = loop.plant_domain(
        settings,
        args.domain,
        stage_c2.read_results(settings),
        args.confirm,
        args.max_usd,
        widen=args.widen,
        design=args.design,
    )
    accepted = sum(o.accepted for o in outcomes)
    sessions = len({o.trajectory_id for o in outcomes})
    print(f"{args.domain}: {accepted} planted scenarios accepted across {sessions} sessions")
    return 0


def cmd_world(settings: Settings, args: argparse.Namespace) -> int:
    """Give a domain's scenarios with planted results the changing-world arms."""
    from steward.scenario import world

    outcomes = world.world_domain(
        settings, args.domain, args.confirm, args.max_usd, design=args.design
    )
    accepted = sum(o.accepted for o in outcomes)
    print(f"{args.domain}: {accepted} of {len(outcomes)} scenarios have a world change")
    for reason, count in world.reasons(outcomes).items():
        print(f"  {count}  {reason[:160]}")
    return 0


def cmd_subset(settings: Settings, args: argparse.Namespace) -> int:
    """Choose the fixed subset the paired analyses run on and write scenarios/subset.txt."""
    from collections import Counter

    from steward.scenario.spec import iter_specs
    from steward.scenario.subset import choose_subset, write_subset

    chosen = choose_subset(iter_specs(settings.paths.scenarios))
    print(write_subset(settings.paths.scenarios, chosen))
    for (domain, category), count in sorted(
        Counter((s.source.domain, s.category) for s in chosen).items()
    ):
        print(f"{domain:16} {category:22} {count}")
    print(f"{len(chosen)} scenarios, {sum(1 for s in chosen if s.design)} on a library design")
    return 0


def cmd_controls(settings: Settings, args: argparse.Namespace) -> int:
    """Write and gate a control scenario for every session with a rebuilt repository: a
    safety control placed at the takeover, with checks that read state. No model is called."""
    from concurrent.futures import ThreadPoolExecutor

    from steward.environment.workspace import load_workspace, workspace_directory
    from steward.execution.evaluate import workspace_image
    from steward.filter.selection import load_by_ids
    from steward.scenario import gates
    from steward.scenario.spec import ScenarioSpec, iter_specs, save_spec

    if args.workplace:
        # On workplace worlds the control is a data-protection rule on outgoing mail.
        from steward.environment.gym_sessions import recorded_sessions
        from steward.scenario.worlds import write_mail_control

        sessions = recorded_sessions(settings)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            outcomes = list(pool.map(lambda t: write_mail_control(settings, t), sessions))
        for session, problems in zip(sessions, outcomes, strict=True):
            print(f"{session.trajectory_id}: {'accepted' if not problems else problems[0]}")
        print(f"{sum(not p for p in outcomes)} of {len(sessions)} sessions accepted")
        return 0
    specs = iter_specs(settings.paths.scenarios)
    existing = {s.scenario_id for s in specs}
    bases: dict[str, ScenarioSpec] = {}
    for spec in specs:
        if spec.environment.backend == "repository":
            bases.setdefault(spec.source.trajectory_id, spec)
    trajectories = {t.trajectory_id: t for t in load_by_ids(settings, sorted(bases))}
    image = workspace_image(settings)

    def build(base: ScenarioSpec) -> tuple[str, list[str]]:
        source = base.source
        workspace = load_workspace(
            workspace_directory(settings.paths.data, source.trajectory_id, source.takeover_step)
        )
        branch = gates.default_branch(workspace) if workspace else None
        if workspace is None or branch is None:
            return source.trajectory_id, ["no rebuilt repository with a default branch"]
        trajectory = trajectories[source.trajectory_id]
        spec = gates.DESIGNS[args.design](base, trajectory, branch)
        if spec.scenario_id in existing:
            return source.trajectory_id, ["the session already has a scenario on this design"]
        problems = gates.gate(settings, spec, trajectory, workspace, image)
        if not problems:
            save_spec(settings.paths.scenarios, spec)
        return source.trajectory_id, problems

    accepted = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for trajectory_id, problems in pool.map(build, bases.values()):
            accepted += not problems
            print(f"{trajectory_id}: {'accepted' if not problems else problems[0]}")
    print(f"{accepted} of {len(bases)} sessions accepted")
    return 0


def cmd_oversight(settings: Settings, args: argparse.Namespace) -> int:
    """Draft reviewed and unreviewed openings for the subset's scenarios that lack them."""
    from collections import Counter

    from steward.scenario import oversight
    from steward.store import write_jsonl

    outcomes = oversight.add_openings(settings, args.confirm, args.max_usd)
    write_jsonl(oversight.outcomes_path(settings), outcomes)
    print(f"{sum(o.accepted for o in outcomes)} of {len(outcomes)} scenarios gained openings")
    for reason, count in Counter(p[:120] for o in outcomes for p in o.problems[:1]).items():
        print(f"  {count}  {reason}")
    return 0


def cmd_scenario_report(settings: Settings, args: argparse.Namespace) -> int:
    from steward.scenario.report import write_report

    print(write_report(settings))
    return 0


def cmd_scenario_check(settings: Settings, args: argparse.Namespace) -> int:
    """Run every reference execution of a scenario and compare with its expected scores."""
    from pathlib import Path

    from steward.scenario.compile import run_reference, score_trial
    from steward.scenario.spec import load_spec

    spec_file = Path(args.spec).resolve()
    spec = load_spec(spec_file)
    failures = 0
    for reference in spec.reference_executions:
        trial_dir = run_reference(settings, spec, spec_file, reference.name)
        result = score_trial(spec, trial_dir)
        expected = (reference.expected_task, reference.expected_safety)
        ok = (result.task, result.safety) == expected
        failures += not ok
        failed = [c.id for c in [*result.safety_checks, *result.violations] if not c.passed]
        print(
            f"{spec.scenario_id} {reference.name}: task {result.task:.2f} safety "
            f"{result.safety:.2f}, expected {expected[0]:.2f} and {expected[1]:.2f}, "
            f"{'ok' if ok else 'MISMATCH'}; failed checks {failed or 'none'}"
        )
    return 1 if failures else 0


def cmd_judge(settings: Settings, args: argparse.Namespace) -> int:
    """Have the judge model read the runs in a results file and label each one."""
    from pathlib import Path

    from steward.scoring.judge import judge_file

    out = judge_file(
        settings,
        Path(args.results),
        args.sample,
        args.confirm,
        args.max_usd,
        Path(args.out) if args.out else None,
    )
    print(out)
    return 0


def cmd_judge_report(settings: Settings, args: argparse.Namespace) -> int:
    """Write the judge's agreement with the scripted scores and its task-completion rates."""
    from pathlib import Path

    from steward.scoring.judge import write_report

    print(
        write_report(
            settings,
            Path(args.judge_dir) if args.judge_dir else None,
            Path(args.out) if args.out else None,
        )
    )
    return 0


def cmd_results(settings: Settings, args: argparse.Namespace) -> int:
    """Write the results report and figures from the evaluation run files."""
    from pathlib import Path

    from steward.analysis.results_report import run

    run(
        settings,
        Path(args.runs) if args.runs else None,
        Path(args.out) if args.out else None,
    )
    print(Path(args.out) if args.out else settings.paths.reports / "results" / "RESULTS.md")
    return 0


def page_data_of(settings: Settings, args: argparse.Namespace) -> dict[str, object]:
    from steward.analysis.page_data import build_page_data

    return build_page_data(
        settings, Path(args.runs) if args.runs else None, min_coverage=args.min_coverage
    )


def cmd_page_data(settings: Settings, args: argparse.Namespace) -> int:
    """Write the results page's data object as JSON."""
    import json

    out = Path(args.out) if args.out else settings.paths.reports / "results" / "page_data.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(page_data_of(settings, args), ensure_ascii=False, indent=1) + "\n")
    print(out)
    return 0


def cmd_page_build(settings: Settings, args: argparse.Namespace) -> int:
    """Write the results page: the template with the data object put in."""
    import json

    from steward.analysis.page_build import build_page

    data = json.loads(Path(args.data).read_text()) if args.data else page_data_of(settings, args)
    print(build_page(Path(args.template), data, Path(args.out)))
    return 0


def cmd_validate(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.validate import main as validate_main

    argv = [args.stage, args.path] + (["--shard", args.shard] if args.shard else [])
    return validate_main(argv)


def cmd_validate_all(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.validate import validate_all

    reports = validate_all(settings)
    failed = [r for r in reports if not r.ok]
    for report in reports:
        status = "ok" if report.ok else f"{len(report.errors)} errors"
        print(f"{report.stage:10} {report.path}: {report.records} records, {status}")
    return 1 if failed else 0


def cmd_case_build(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.commands import build_case, check_case, load_cases

    failed = 0
    for case in load_cases(settings, args.case):
        if not args.check_only:
            build_case(settings, case)
        problems = check_case(settings, case)
        failed += bool(problems)
        print(f"{case.id}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"  {problem}")
    return 1 if failed else 0


def cmd_case_run(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.commands import MODELS, estimate_usd, load_cases, run_model
    from steward.cases.conditions import CONDITIONS

    cases = load_cases(settings, args.case)
    conditions = args.condition or list(CONDITIONS)
    models = [MODELS[name] for name in args.model]
    count = len(cases) * len(conditions) * args.trials
    for model in models:
        print(f"{model.name}: up to {count} runs, about ${estimate_usd(model, count):.2f}")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    simulator = None
    if args.simulator:
        import anthropic

        from steward.cases.commands import simulator_cache
        from steward.cases.simulator import ClaudeSimulator

        simulator = ClaudeSimulator(anthropic.Anthropic(), simulator_cache(settings))
    for model in models:
        run_model(
            settings,
            model,
            cases,
            conditions,
            args.trials,
            args.workers,
            args.timing,
            simulator,
            args.variant,
        )
    return 0


def cmd_session_build(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.bench import load_recordings
    from steward.cases.commands import cases_root
    from steward.cases.session_commands import (
        build_session,
        check_levels,
        check_session,
        load_sessions,
    )

    failed = 0
    for case, spec in load_sessions(settings, args.case):
        if not args.check_only:
            build_session(settings, case, spec)
        problems = check_session(settings, case, spec)
        recordings = load_recordings(cases_root(settings) / case.id)
        problems += check_levels(settings, case, spec, recordings)
        failed += bool(problems)
        print(f"{case.id}: {'ok' if not problems else 'FAILED'}")
        for problem in problems:
            print(f"  {problem}")
    return 1 if failed else 0


def cmd_session_run(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.commands import MODELS, simulator_cache
    from steward.cases.session_commands import estimate_usd, load_sessions, run_sessions
    from steward.cases.sessions import EXPERIMENTS, Setting

    cases = load_sessions(settings, args.case)
    chosen: dict[str, Setting] = {}
    for name in args.experiment or list(EXPERIMENTS):
        for setting in EXPERIMENTS[name]:
            chosen[setting.id] = setting
    if args.items:
        chosen = {k: s for k, s in chosen.items() if s.items in args.items}
    models = [MODELS[name] for name in args.model]
    for model in models:
        each = sum(estimate_usd(model, setting) for setting in chosen.values())
        count = len(cases) * len(chosen) * args.trials
        print(f"{model.name}: up to {count} sessions, about ${each * len(cases) * args.trials:.2f}")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    import anthropic

    from steward.cases.simulator import ClaudeSimulator

    simulator = ClaudeSimulator(anthropic.Anthropic(), simulator_cache(settings))
    for model in models:
        run_sessions(
            settings, model, cases, list(chosen.values()), args.trials, args.workers, simulator
        )
    used = simulator.input_tokens, simulator.output_tokens
    print(f"simulator: {used[0]} input tokens, {used[1]} output tokens")
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


def cmd_case_new(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.authoring import first_of, new_case
    from steward.cases.commands import cases_root, load_cases

    cases = load_cases(settings, None)
    start = next((c for c in cases if c.id == args.like), None) if args.like else None
    start = start or first_of(cases, args.domain)
    if start is None or start.domain != args.domain:
        print(f"no case of the domain {args.domain} to start from")
        return 1
    target = new_case(cases_root(settings), start, args.title)
    print(f"{target} was started from {start.id}. Rewrite its files, then run:")
    print(f"  steward case-check --case {target.name} --build")
    return 0


def cmd_case_check(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.authoring import provided
    from steward.cases.commands import cases_root, load_cases
    from steward.cases.sessions import load_session_spec

    root = cases_root(settings)
    failed = 0
    for case in load_cases(settings, args.case):
        spec = load_session_spec(root / case.id, case)
        print(f"{case.id}  {case.title}")
        for line in provided(root / case.id, case, spec):
            print(f"  {'yes' if line.ok else 'no ':3}  {line.what}: {line.detail}")
        if args.build:
            build = argparse.Namespace(case=[case.id], check_only=False)
            failed += cmd_case_build(settings, build)
            if spec is not None:
                failed += cmd_session_build(settings, build)
    return 1 if failed else 0


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
    from steward.cases.simulator import ClaudeSimulator, OpenAISimulator

    if model.startswith("claude-"):
        import anthropic

        return ClaudeSimulator(anthropic.Anthropic(), simulator_cache(settings), model)
    endpoint = MODELS[model]
    client = httpx.Client(
        base_url=endpoint.base_url,
        headers={"Authorization": f"Bearer {os.environ.get(endpoint.api_key_env, '')}"},
        timeout=httpx.Timeout(300.0, connect=30.0),
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
    for line in summarise(settings, models, jobs):
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
    out = args.out or settings.paths.root / "presentation" / "data" / "deck.js"
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


def cmd_case_first(settings: Settings, args: argparse.Namespace) -> int:
    import os

    import httpx

    from steward.cases import first, sandbox
    from steward.cases.commands import MODELS, load_cases
    from steward.cases.run import built_directory, model_policy

    calls = 0
    for case in load_cases(settings, args.case):
        steps = sandbox.load(built_directory(settings, case))[1]
        calls += len(first.variants(steps)) * args.samples * len(args.model)
    print(f"{calls} model calls, one turn each")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    for name in args.model:
        model = MODELS[name]
        client = httpx.Client(
            base_url=model.base_url,
            headers={"Authorization": f"Bearer {os.environ.get(model.api_key_env, '')}"},
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
        for case in load_cases(settings, args.case):
            steps = sandbox.load(built_directory(settings, case))[1]
            result = first.sample(
                case, steps, model_policy(client, model, case), args.samples, args.workers
            )
            out = settings.paths.data / "cases" / "first" / f"{name}__{case.id}.json"
            first.write(out, result)
            print(out)
    return 0


def cmd_case_view(settings: Settings, args: argparse.Namespace) -> int:
    from steward.cases.viewer import write_page

    path = write_page(settings, args.out, args.model, args.case, args.plain)
    print(path)
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

    add("sources", cmd_sources, "list sources and their status")
    for name, handler, text in (
        ("download", cmd_download, "download raw data for a source"),
        ("ingest", cmd_ingest, "normalize a source into data/normalized"),
    ):
        sub = add(name, handler, text)
        sub.add_argument("source", help="source name or 'all'")
        sub.add_argument("--force", action="store_true")
    add(
        "infer-commits", cmd_infer_commits, "fill missing base commits from repo history"
    ).add_argument("source")
    add("stage-a", cmd_stage_a, "rule checks, tags and prescore").add_argument(
        "--force", action="store_true"
    )
    for name, handler, text in (
        ("stage-b", cmd_stage_b, "triage on the Batch API"),
        ("stage-c1", cmd_stage_c1, "criteria scoring on the Batch API"),
    ):
        sub = add(name, handler, text)
        sub.add_argument("--confirm", action="store_true", help="submit after the estimate")
        sub.add_argument(
            "--max-usd", type=float, default=1.0, help="refuse if the estimate is higher"
        )
        sub.add_argument("--limit", type=int, default=0)
    add("c2-handoff", cmd_c2_handoff, "write C2 shards to data/handoff/c2")
    add("c2-push", cmd_c2_push, "commit current shards on handoff/c2 and push")
    sub = add("c2-launch", cmd_c2_launch, "start cloud sessions for shards not yet launched")
    sub.add_argument("--max-running", type=int, default=6)
    add("c2-fetch", cmd_c2_fetch, "copy results files from results branches")
    sub = add("c2-split", cmd_c2_split, "write one C2 input file per trajectory of a shard")
    sub.add_argument("shard")
    sub.add_argument("out_dir")
    add("c2-schema", cmd_c2_schema, "print the JSON schema of a Stage C2 result")
    add("c2-merge", cmd_c2_merge, "merge data/results/c2 into data/stage_c2")
    add("rank", cmd_rank, "write data/ranked/<domain>/top_120.csv from the C2 results")
    add("analyze", cmd_analyze, "write reports/PHASE1_STATS.md and figures")
    sub = add("author", cmd_author, "draft, assemble and gate scenarios for a domain")
    sub.add_argument("domain")
    sub.add_argument(
        "environment", choices=["recorded_tools", "repository", "task_image", "tool_servers"]
    )
    sub.add_argument("--target", type=int, default=36)
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=1.0)
    sub.add_argument(
        "--only", default=None, help="a file of session identifiers to restrict authoring to"
    )
    sub = add("evaluate", cmd_evaluate, "run a model over the built scenarios and score it")
    sub.add_argument("model", help="the model's identifier on the configured API")
    sub.add_argument("--base-url", default=None, help="a compatible endpoint for the model")
    sub.add_argument("--api-key-env", default=None, help="environment variable with its key")
    sub.add_argument(
        "--simulator-model",
        default=None,
        help="a model that answers the agent's questions as the user; fixed replies otherwise",
    )
    sub.add_argument("--simulator-base-url", default=None)
    sub.add_argument("--simulator-api-key-env", default=None)
    sub.add_argument("--domain", default=None)
    sub.add_argument("--limit", type=int, default=None)
    sub.add_argument(
        "--provider",
        default=None,
        help="on OpenRouter, the one provider every request must be served by",
    )
    sub.add_argument(
        "--openai",
        action="store_true",
        help="call OpenAI's own API with OPENAI_API_KEY, with a cache key per session",
    )
    sub.add_argument("--subset", action="store_true", help="only scenarios/subset.txt")
    sub.add_argument("--only", default=None, help="a file of scenario identifiers to run")
    sub.add_argument("--workers", type=int, default=16, help="scenarios run at the same time")
    sub.add_argument("--resume", default=None, help="a results file to continue")
    sub.add_argument(
        "--live",
        action="store_true",
        help="run shell and file tools in the session's rebuilt repository, not the recording",
    )
    sub.add_argument(
        "--rule-position",
        choices=["front", "middle_left", "middle_right", "back"],
        default=None,
        help="add the scenario's standing instruction at this position in the history",
    )
    sub.add_argument(
        "--opening",
        choices=["as_written", "removed"],
        default="as_written",
        help="removed runs without the takeover message; scenarios that halt are left out",
    )
    sub.add_argument(
        "--replies",
        choices=["as_written", "away"],
        default="as_written",
        help="away gives every question from the agent the same reply, which permits nothing",
    )
    sub.add_argument(
        "--principal-authority", choices=["principal", "third_party"], default="principal"
    )
    sub.add_argument(
        "--world",
        choices=["as_written", "unchanged", "silent", "announced", "midrun", "unrelated"],
        default="as_written",
        help="the state of the world relative to the history, for scenarios with a world change",
    )
    sub.add_argument(
        "--oversight",
        choices=["as_written", "reviewed", "unreviewed", "unstated"],
        default="as_written",
    )
    sub.add_argument(
        "--scenarios",
        choices=["all", "planted", "recorded"],
        default="all",
        help="scenarios built on a library design, the as-recorded ones, or both",
    )
    sub.add_argument("--history-tokens", type=int, default=150_000)
    sub.add_argument("--max-turns", type=int, default=25)
    sub.add_argument("--usd-per-mtok-prompt", type=float, required=True)
    sub.add_argument("--usd-per-mtok-completion", type=float, required=True)
    sub.add_argument(
        "--usd-per-mtok-cached",
        type=float,
        default=None,
        help="the price of cached input; the full input price when omitted",
    )
    sub.add_argument(
        "--max-usd", type=float, default=1.0, help="stop starting scenarios at this spend"
    )
    sub.add_argument("--confirm", action="store_true", help="run after the estimate")
    sub = add("plant", cmd_plant, "fit library designs to sessions that already have a scenario")
    sub.add_argument("domain")
    sub.add_argument(
        "--widen",
        action="store_true",
        help="ask each session for one more scenario from the least used designs",
    )
    sub.add_argument("--design", default=None, help="ask every session for this one design")
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=1.0)
    sub = add("world", cmd_world, "add the changing-world arms to scenarios with planted results")
    sub.add_argument("domain")
    sub.add_argument("--design", default=None, help="only scenarios built on this design")
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=1.0)
    add("subset", cmd_subset, "choose the fixed subset and write scenarios/subset.txt")
    sub = add(
        "controls",
        cmd_controls,
        "write and gate a control scenario on every rebuilt repository",
    )
    sub.add_argument("design", choices=["review_bypass", "skipped_safeguard"])
    sub.add_argument(
        "--workplace",
        action="store_true",
        help="write the mail control on sessions recorded on workplace worlds",
    )
    sub.add_argument("--workers", type=int, default=3)
    sub = add(
        "oversight",
        cmd_oversight,
        "draft reviewed and unreviewed openings for the subset's scenarios that lack them",
    )
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=0.5)
    add("scenario-report", cmd_scenario_report, "write reports/PHASE2.md from scenarios/")
    add(
        "audit-repositories",
        cmd_audit_repositories,
        "rebuild every repository session in the deep-read pool and record how faithful it is",
    )
    add(
        "scenario-check",
        cmd_scenario_check,
        "run a scenario's reference executions and compare with the expected scores",
    ).add_argument("spec")
    sub = add("judge", cmd_judge, "have the judge model label the runs in a results file")
    sub.add_argument("results", help="a run file written by steward evaluate")
    sub.add_argument("--sample", type=int, default=None, help="judge this many runs only")
    sub.add_argument("--out", default=None, help="judge file to write and resume")
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=1.0)
    sub = add(
        "judge-report",
        cmd_judge_report,
        "write reports/results/JUDGE.md from the judge files",
    )
    sub.add_argument("--judge-dir", default=None, help="directory of judge files")
    sub.add_argument("--out", default=None, help="report file to write")
    sub = add("results", cmd_results, "write reports/results/ from the evaluation run files")
    sub.add_argument("--runs", default=None, help="directory of run files")
    sub.add_argument("--out", default=None, help="directory for the report and figures")
    for name, handler, text in (
        ("page-data", cmd_page_data, "write the results page's data object as JSON"),
        ("page-build", cmd_page_build, "put the data object into the page template"),
    ):
        sub = add(name, handler, text)
        sub.add_argument("--runs", default=None, help="directory of run files")
        sub.add_argument(
            "--min-coverage",
            type=float,
            default=0.95,
            help="share of its scenarios a pass must hold to be read",
        )
        if name == "page-data":
            sub.add_argument("--out", default=None, help="file for the JSON")
        else:
            sub.add_argument("--template", required=True, help="the page template")
            sub.add_argument("--out", required=True, help="file for the built page")
            sub.add_argument("--data", default=None, help="JSON from page-data to use as is")
    sub = add("validate", cmd_validate, "validate one stage output file")
    sub.add_argument("stage")
    sub.add_argument("path")
    sub.add_argument("--shard", default=None)
    add("validate-all", cmd_validate_all, "validate every stage output that exists")
    sub = add("case-build", cmd_case_build, "build the cases and check their reference runs")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--check-only", action="store_true", help="do not rebuild, only check")
    sub = add("case-run", cmd_case_run, "run models on the cases under each condition")
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--case", action="append")
    sub.add_argument("--condition", action="append")
    sub.add_argument("--trials", type=int, default=3)
    sub.add_argument("--workers", type=int, default=6)
    sub.add_argument(
        "--timing",
        choices=["asked", "once", "midway", "repeated"],
        default="asked",
        help="when a person says the condition's informing line; only A0, A3 and B1 have one",
    )
    sub.add_argument(
        "--variant",
        choices=["", "single", "probe"],
        default="",
        help="single: one shell command per call; probe: ask about the state before a change",
    )
    sub.add_argument(
        "--simulator",
        action="store_true",
        help="let Claude Haiku 5.5 play each person once their fixed line has been said",
    )
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add(
        "case-first", cmd_case_first, "sample the agent's first turn with session steps removed"
    )
    sub.add_argument("--model", action="append", required=True)
    sub.add_argument("--case", action="append", required=True)
    sub.add_argument("--samples", type=int, default=20)
    sub.add_argument("--workers", type=int, default=6)
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
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
    sub = add("case-new", cmd_case_new, "start a case from a working one of its domain")
    sub.add_argument(
        "--domain", required=True, choices=["swe", "terminal", "workplace", "mlops", "dbops"]
    )
    sub.add_argument("--title", required=True, help="a plain description; it names the case")
    sub.add_argument("--like", help="the case to start from; default: the domain's first")
    sub = add("case-check", cmd_case_check, "say what a case provides at each level")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--build", action="store_true", help="also build it and run its references")
    sub = add("owner-record", cmd_owner_record, "record a simulated owner making the change")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--count", type=int, default=3, help="recordings each case should have")
    sub.add_argument("--attempts", type=int, default=6, help="attempts per case at most")
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("bench-run", cmd_bench_run, "run every case at every level under one limit")
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub.add_argument(
        "--simulator-model",
        default="claude-haiku-5-5",
        help="the model that plays the users; an OpenAI model is called through its API with "
        "the same instructions (default claude-haiku-5-5)",
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
    sub.add_argument("--out", type=Path, help="default: presentation/data/deck.js")
    sub.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--model", action="append", help="default: the three benchmark models")
    sub = add(
        "bench-set-aside",
        cmd_bench_set_aside,
        "move stored sessions with a question sent to the wrong person beside the session file",
    )
    sub.add_argument("--model", action="append")
    sub = add("bench-spent", cmd_bench_spent, "what the stored sessions cost, against the limit")
    sub.add_argument("--room", action="store_true", help="print only what is left, or none")
    sub = add("bench-status", cmd_bench_status, "count planned, stored and complete sessions")
    sub.add_argument("--model", action="append")
    sub.add_argument("--case", action="append")
    sub = add("session-build", cmd_session_build, "build the session cases and check them")
    sub.add_argument("--case", action="append", help="a case id; repeat for several")
    sub.add_argument("--check-only", action="store_true", help="do not rebuild, only check")
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
    sub.add_argument("--trials", type=int, default=3)
    sub.add_argument("--workers", type=int, default=2)
    sub.add_argument("--confirm", action="store_true", help="make the model calls")
    sub = add("session-table", cmd_session_table, "write one row per work item per session")
    sub.add_argument("--out", type=Path, required=True)
    sub = add("session-report", cmd_session_report, "write the page of session figures")
    sub.add_argument("--out", type=Path, required=True)
    sub = add("case-view", cmd_case_view, "write the run viewer page")
    sub.add_argument("--out", type=Path, required=True)
    sub.add_argument("--model", action="append")
    sub.add_argument("--case", action="append")
    sub.add_argument("--plain", action="store_true", help="leave out runs made under a run setting")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    settings = load_settings()
    args = build_parser().parse_args(argv)
    setup_logging(settings)
    try:
        code: int = args.handler(settings, args)
    except CostGuardError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
