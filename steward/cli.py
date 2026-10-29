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
    from steward.sources.registry import LOADERS, UNAVAILABLE

    for name, source in sorted(LOADERS.items()):
        print(f"{name:28} {source.info.domain:16} loader      {source.info.location}")
    for name, entry in sorted(UNAVAILABLE.items()):
        print(f"{name:28} {entry.domain:16} unavailable {entry.reason}")
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


def cmd_pilot(settings: Settings, args: argparse.Namespace) -> int:
    from steward import pilot

    if not args.confirm:
        job = pilot.estimate_only(settings)
        print(f"Pilot Stage B estimate: {job.requests} requests, ${job.usd:.4f}. Pass --confirm.")
        return 1
    numbers = pilot.run(settings, confirm=True, max_usd=args.max_usd)
    print(numbers.model_dump_json(indent=2))
    print(f"Projected full B + C1: ${numbers.projected_total_usd:.2f}")
    return 0


def cmd_pilot_report(settings: Settings, args: argparse.Namespace) -> int:
    from steward.pilot_report import write_pilot_report

    write_pilot_report(settings)
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
    from steward.filter import c2_handoff

    paths = settings.paths
    if args.pilot:
        shards = c2_handoff.build_shards(
            settings,
            paths.pilot / "stage_c1.jsonl",
            paths.pilot / "stage_b.jsonl",
            paths.handoff_c2_pilot,
        )
    else:
        shards = c2_handoff.build_shards(
            settings,
            paths.stage_c1 / "results.jsonl",
            paths.stage_b / "results.jsonl",
            paths.handoff_c2,
        )
    for path in shards:
        print(path)
    return 0


def cmd_c2_merge(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import c2_handoff

    paths = settings.paths
    if args.pilot:
        c2_handoff.merge_results(
            paths.handoff_c2_pilot, paths.results_c2_pilot, paths.pilot / "stage_c2.jsonl"
        )
    else:
        c2_handoff.merge_results(
            paths.handoff_c2, paths.results_c2, paths.stage_c2 / "results.jsonl"
        )
    return 0


def _c2_locations(settings: Settings, pilot: bool) -> tuple[Path, Path, str]:
    paths = settings.paths
    if pilot:
        return paths.handoff_c2_pilot, paths.results_c2_pilot, "results/c2-pilot-shard-"
    return paths.handoff_c2, paths.results_c2, "results/c2-shard-"


def cmd_c2_push(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.c2_branches import push_handoff

    dirs = [d for d in (settings.paths.handoff_c2, settings.paths.handoff_c2_pilot) if d.exists()]
    print(push_handoff(settings.paths.root, dirs, "Update Stage C2 handoff shards"))
    return 0


def cmd_c2_launch(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.c2_launch import launch

    handoff, results, prefix = _c2_locations(settings, args.pilot)
    started = launch(
        settings.paths.root,
        handoff,
        results,
        prefix,
        handoff.parent / f"{handoff.name}_launches.jsonl",
        min(args.max_running, settings.stage_c2.max_parallel_sessions),
        settings.paths.logs / "cloud",
    )
    for entry in started:
        print(entry.shard, entry.results_branch, entry.session_url)
    return 0


def cmd_c2_fetch(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.c2_branches import fetch_results

    _, results, prefix = _c2_locations(settings, args.pilot)
    for path in fetch_results(settings.paths.root, prefix, results):
        print(path)
    return 0


def cmd_c2_split(settings: Settings, args: argparse.Namespace) -> int:
    from pathlib import Path

    from steward.filter import c2_handoff

    for path in c2_handoff.split_shard(Path(args.shard), Path(args.out_dir)):
        print(path)
    return 0


def cmd_c2_schema(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import c2_handoff

    print(c2_handoff.c2_json_schema())
    return 0


def cmd_rank(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter import c2_handoff, rank, stage_c1
    from steward.filter.stage_a import read_results
    from steward.store import read_meta, written_sources

    metas = {
        m.trajectory_id: m
        for source in written_sources(settings.paths.normalized)
        for m in read_meta(settings.paths.normalized, source)
    }
    domains = sorted({m.domain for m in metas.values()})
    rank.run(
        settings,
        stage_c1.read_results(settings.paths.stage_c1 / "results.jsonl"),
        c2_handoff.read_results(settings),
        read_results(settings),
        metas,
        domains,
    )
    return 0


def cmd_analyze(settings: Settings, args: argparse.Namespace) -> int:
    from steward.analysis.report import write_report

    write_report(settings)
    return 0


def cmd_sources_status(settings: Settings, args: argparse.Namespace) -> int:
    from steward.analysis.sources_status import write_sources_status

    write_sources_status(settings)
    return 0


def cmd_validate(settings: Settings, args: argparse.Namespace) -> int:
    from steward.filter.validate import main as validate_main

    argv = [args.stage, args.path] + (["--shard", args.shard] if args.shard else [])
    return validate_main(argv)


def cmd_validate_all(settings: Settings, args: argparse.Namespace) -> int:
    from steward.analysis.validation import validate_all

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
        ("pilot", cmd_pilot, "run the 40-trajectory pilot through B and C1"),
        ("stage-b", cmd_stage_b, "triage on the Batch API"),
        ("stage-c1", cmd_stage_c1, "criteria scoring on the Batch API"),
    ):
        sub = add(name, handler, text)
        sub.add_argument("--confirm", action="store_true", help="submit after the estimate")
        sub.add_argument(
            "--max-usd", type=float, default=1.0, help="refuse if the estimate is higher"
        )
        if name != "pilot":
            sub.add_argument("--limit", type=int, default=0)
    add("c2-handoff", cmd_c2_handoff, "write C2 shards to data/handoff/c2").add_argument(
        "--pilot", action="store_true", help="build shards from the pilot C1 results"
    )
    add("c2-push", cmd_c2_push, "commit current shards on handoff/c2 and push")
    sub = add("c2-launch", cmd_c2_launch, "start cloud sessions for shards not yet launched")
    sub.add_argument("--pilot", action="store_true")
    sub.add_argument("--max-running", type=int, default=6)
    add("c2-fetch", cmd_c2_fetch, "copy results files from results branches").add_argument(
        "--pilot", action="store_true"
    )
    sub = add("c2-split", cmd_c2_split, "write one C2 input file per trajectory of a shard")
    sub.add_argument("shard")
    sub.add_argument("out_dir")
    add("c2-schema", cmd_c2_schema, "print the JSON schema of a Stage C2 result")
    add("c2-merge", cmd_c2_merge, "merge data/results/c2 into data/stage_c2").add_argument(
        "--pilot", action="store_true", help="merge the pilot results into data/pilot"
    )
    add("pilot-report", cmd_pilot_report, "write reports/PILOT.md from the pilot outputs")
    add("rank", cmd_rank, "write data/ranked/<domain>/<axis>.csv")
    add("analyze", cmd_analyze, "write reports/PHASE1_REPORT.md and figures")
    add("sources-status", cmd_sources_status, "write reports/SOURCES_STATUS.md")
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
