"""Stage C2 handoff: select the C1 top per domain, write shards, merge cloud results (brief 3.2)."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError

from steward.config import Settings
from steward.filter.redact import redact
from steward.filter.selection import load_by_ids
from steward.filter.stage_a import read_results as read_stage_a
from steward.filter.stage_b import read_results as read_stage_b
from steward.filter.stage_c1 import render_for_scoring
from steward.schema import AXES, C2Input, StageC1Result, StageC2Output, StageC2Result
from steward.store import read_jsonl, write_jsonl

log = logging.getLogger(__name__)


def top_two_axis_sum(result: StageC1Result) -> int:
    assert result.output is not None
    scores = sorted((result.output.scores[a].score for a in AXES), reverse=True)
    return scores[0] + scores[1]


def select_inputs(
    settings: Settings, c1: list[StageC1Result], agent_actions: dict[str, int]
) -> list[StageC1Result]:
    """Top N per domain by the sum of the two highest axis scores, ties by agent actions."""
    by_domain: dict[str, list[StageC1Result]] = defaultdict(list)
    for result in c1:
        if result.output is not None:
            by_domain[result.domain].append(result)
    chosen: list[StageC1Result] = []
    for domain in sorted(by_domain):
        ranked = sorted(
            by_domain[domain],
            key=lambda r: (
                -top_two_axis_sum(r),
                -agent_actions.get(r.trajectory_id, 0),
                r.trajectory_id,
            ),
        )
        chosen.extend(ranked[: settings.stage_c2.per_domain])
    return chosen


def shard_path(settings: Settings, number: int) -> Path:
    return settings.paths.handoff_c2 / f"shard_{number:03d}.jsonl"


def build_shards(settings: Settings, c1_path: Path, b_path: Path) -> list[Path]:
    stage_a = {r.trajectory_id: r for r in read_stage_a(settings)}
    stage_b = {r.trajectory_id: r for r in read_stage_b(b_path)}
    c1 = read_jsonl(c1_path, StageC1Result)
    chosen = select_inputs(settings, c1, {k: v.agent_actions for k, v in stage_a.items()})
    by_id = {r.trajectory_id: r for r in chosen}
    lines: list[C2Input] = []
    for trajectory in load_by_ids(settings, list(by_id)):
        result = by_id[trajectory.trajectory_id]
        assert result.output is not None
        rendering = render_for_scoring(
            settings,
            trajectory,
            stage_a[trajectory.trajectory_id],
            stage_b.get(trajectory.trajectory_id),
        )
        lines.append(
            C2Input(
                trajectory_id=trajectory.trajectory_id,
                source=trajectory.source,
                domain=result.domain,
                rendering=redact(rendering),
                c1_output=result.output,
            )
        )
    lines.sort(key=lambda line: (line.domain, line.trajectory_id))
    for old in settings.paths.handoff_c2.glob("shard_*.jsonl"):
        old.unlink()
    shards: list[list[C2Input]] = [[]]
    size = 0
    for line in lines:
        line_bytes = len(line.model_dump_json()) + 1
        full = len(shards[-1]) >= settings.stage_c2.shard_max_trajectories
        if shards[-1] and (full or size + line_bytes > settings.stage_c2.shard_max_bytes):
            shards.append([])
            size = 0
        shards[-1].append(line)
        size += line_bytes
    paths = []
    for number, shard in enumerate(s for s in shards if s):
        path = shard_path(settings, number)
        write_jsonl(path, shard)
        paths.append(path)
    log.info("c2 handoff: %d trajectories in %d shards", len(lines), len(paths))
    return paths


def split_shard(shard: Path, out_dir: Path) -> list[Path]:
    """Write one input file per trajectory of a shard, for one C2 subagent each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, line in enumerate(read_jsonl(shard, C2Input)):
        path = out_dir / f"{number:03d}.md"
        path.write_text(
            f"trajectory_id: {line.trajectory_id}\n\n"
            f"FIRST-PASS (C1) RESULT\n{line.c1_output.model_dump_json(indent=1)}\n\n"
            f"TRAJECTORY\n{line.rendering}\n"
        )
        paths.append(path)
    return paths


def c2_json_schema() -> str:
    return json.dumps(StageC2Output.model_json_schema(), indent=1)


def merge_results(settings: Settings) -> list[StageC2Result]:
    """Merge data/results/c2/*.jsonl into data/stage_c2/results.jsonl, keeping valid lines."""
    inputs: dict[str, C2Input] = {}
    for path in sorted(settings.paths.handoff_c2.glob("shard_*.jsonl")):
        for line in read_jsonl(path, C2Input):
            inputs[line.trajectory_id] = line
    merged: dict[str, StageC2Result] = {}
    invalid = 0
    for path in sorted(settings.paths.results_c2.glob("*.jsonl")):
        with path.open() as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    output = StageC2Output.model_validate_json(raw)
                except ValidationError:
                    invalid += 1
                    continue
                source_line = inputs.get(output.trajectory_id)
                if source_line is None:
                    invalid += 1
                    continue
                merged[output.trajectory_id] = StageC2Result(
                    trajectory_id=output.trajectory_id,
                    source=source_line.source,
                    domain=source_line.domain,
                    output=output,
                )
    results = [merged[k] for k in sorted(merged)]
    write_jsonl(settings.paths.stage_c2 / "results.jsonl", results)
    log.info("c2 merge: %d valid results, %d invalid lines", len(results), invalid)
    return results


def read_results(settings: Settings) -> list[StageC2Result]:
    return read_jsonl(settings.paths.stage_c2 / "results.jsonl", StageC2Result)
