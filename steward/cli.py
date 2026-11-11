"""Single entry point: steward <command>."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from steward.config import Settings, load_settings
from steward.filter.batch import CostGuardError

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
    from steward.schema import Trajectory
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

    def container_gate(spec: ScenarioSpec, trajectory: Trajectory) -> list[str]:
        return gate_container(settings, spec, settings.paths.data / "phase2" / "gating")

    eligible = None
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
    outcomes = loop.author_domain(
        settings,
        args.domain,
        stage_c2.read_results(settings),
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

    from steward.execution.evaluate import Condition, evaluate, summarize

    condition = Condition(
        instruction_recency=args.instruction_recency,
        principal_authority=args.principal_authority,
        environment_drift=args.environment_drift,
        history_tokens=args.history_tokens,
        max_turns=args.max_turns,
    )
    out = evaluate(
        settings,
        args.model,
        condition,
        args.domain,
        args.limit,
        args.usd_per_mtok_prompt,
        args.usd_per_mtok_completion,
        args.max_usd,
        args.confirm,
    )
    print(out)
    print(json.dumps(summarize(out), indent=2))
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
    sub.add_argument("environment", choices=["recorded_tools", "repository", "task_image"])
    sub.add_argument("--target", type=int, default=36)
    sub.add_argument("--confirm", action="store_true", help="call the model after the estimate")
    sub.add_argument("--max-usd", type=float, default=1.0)
    sub = add("evaluate", cmd_evaluate, "run a model over the built scenarios and score it")
    sub.add_argument("model", help="the model's identifier on the configured API")
    sub.add_argument("--domain", default=None)
    sub.add_argument("--limit", type=int, default=None)
    sub.add_argument("--instruction-recency", choices=["early", "mid", "late"], default=None)
    sub.add_argument(
        "--principal-authority", choices=["principal", "third_party"], default="principal"
    )
    sub.add_argument(
        "--environment-drift",
        choices=["unchanged", "silent", "announced", "irrelevant"],
        default="unchanged",
    )
    sub.add_argument("--history-tokens", type=int, default=150_000)
    sub.add_argument("--max-turns", type=int, default=25)
    sub.add_argument("--usd-per-mtok-prompt", type=float, required=True)
    sub.add_argument("--usd-per-mtok-completion", type=float, required=True)
    sub.add_argument("--max-usd", type=float, default=1.0)
    sub.add_argument("--confirm", action="store_true", help="run after the estimate")
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
    sub = add("validate", cmd_validate, "validate one stage output file")
    sub.add_argument("stage")
    sub.add_argument("path")
    sub.add_argument("--shard", default=None)
    add("validate-all", cmd_validate_all, "validate every stage output that exists")
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
