"""Schema validation for every stage output.

Usage: python -m steward.filter.validate <stage> <path> [--shard <c2 input shard>]
Stages: normalized, a, b, c1, c2, c2_input, c2_merged, ranked.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ValidationError

from steward.filter.rank import COLUMNS as RANKED_COLUMNS
from steward.schema import (
    C2Input,
    StageAResult,
    StageBResult,
    StageC1Result,
    StageC2Output,
    StageC2Result,
    Step,
    TrajectoryMeta,
)

TRAJECTORY_ID = re.compile(r"^[a-z0-9_]+:\S+$")


class Report(BaseModel):
    stage: str
    path: str
    records: int = 0
    errors: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, message: str) -> None:
        if len(self.errors) < 50:
            self.errors.append(message)


def _check_ids(report: Report, ids: list[str]) -> None:
    seen: set[str] = set()
    for trajectory_id in ids:
        if not TRAJECTORY_ID.match(trajectory_id):
            report.add(f"bad trajectory_id {trajectory_id!r}")
        if trajectory_id in seen:
            report.add(f"duplicate trajectory_id {trajectory_id}")
        seen.add(trajectory_id)


def validate_jsonl(report: Report, path: Path, model: type[BaseModel]) -> list[BaseModel]:
    records: list[BaseModel] = []
    with path.open() as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(model.model_validate_json(line))
            except ValidationError as error:
                report.add(f"line {number}: {str(error).splitlines()[0]} {str(error)[:200]}")
    report.records = len(records)
    _check_ids(report, [str(r.model_dump().get("trajectory_id")) for r in records])
    return records


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def check_c2_against_shard(report: Report, outputs: list[StageC2Output], shard: Path) -> None:
    inputs = {
        line.trajectory_id: line
        for line in (C2Input.model_validate_json(x) for x in shard.open() if x.strip())
    }
    for output in outputs:
        source = inputs.get(output.trajectory_id)
        if source is None:
            report.add(f"{output.trajectory_id}: not in shard")
            continue
        rendering = normalize_space(source.rendering)
        for criterion, score in output.scores.items():
            if score.excerpt and normalize_space(score.excerpt) not in rendering:
                report.add(f"{output.trajectory_id}: {criterion} excerpt is not verbatim")
    missing = set(inputs) - {o.trajectory_id for o in outputs}
    for trajectory_id in sorted(missing):
        report.add(f"{trajectory_id}: no result")


def validate_normalized(report: Report, path: Path) -> None:
    frame = pd.read_parquet(path / "meta.parquet")
    metas = []
    for record in frame.astype(object).where(frame.notna(), None).to_dict("records"):
        try:
            metas.append(TrajectoryMeta.model_validate(record))
        except ValidationError as error:
            report.add(str(error)[:200])
    report.records = len(metas)
    _check_ids(report, [m.trajectory_id for m in metas])
    expected = {m.trajectory_id: m.n_steps for m in metas}
    counts: dict[str, int] = {}
    last: dict[str, int] = {}
    with (path / "steps.jsonl").open() as handle:
        for number, line in enumerate(handle, start=1):
            record = json.loads(line)
            trajectory_id = record.pop("trajectory_id")
            step = Step.model_validate(record)
            if step.index != last.get(trajectory_id, -1) + 1:
                report.add(f"line {number}: {trajectory_id} step {step.index} not contiguous")
            last[trajectory_id] = step.index
            counts[trajectory_id] = counts.get(trajectory_id, 0) + 1
    for trajectory_id, n_steps in expected.items():
        if counts.get(trajectory_id, 0) != n_steps:
            report.add(
                f"{trajectory_id}: {counts.get(trajectory_id, 0)} steps, meta says {n_steps}"
            )


def validate_stage_a(report: Report, path: Path) -> None:
    frame = pd.read_parquet(path)
    for record in frame.to_dict("records"):
        record["tags"] = [dict(t) for t in record["tags"]]
        record["failed_check"] = (
            record["failed_check"] if isinstance(record["failed_check"], str) else None
        )
        try:
            result = StageAResult.model_validate(record)
        except ValidationError as error:
            report.add(str(error)[:200])
            continue
        if result.passed != (result.failed_check is None):
            report.add(f"{result.trajectory_id}: passed and failed_check disagree")
    report.records = len(frame)
    _check_ids(report, list(frame["trajectory_id"]))


def validate_ranked(report: Report, path: Path) -> None:
    frame = pd.read_csv(path)
    if list(frame.columns) != RANKED_COLUMNS:
        report.add(f"columns {list(frame.columns)} differ from {RANKED_COLUMNS}")
        return
    report.records = len(frame)
    if list(frame["rank"]) != list(range(1, len(frame) + 1)):
        report.add("ranks are not 1..n")
    scores = list(frame["axis_score"])
    if scores != sorted(scores, reverse=True):
        report.add("axis_score is not sorted descending")
    if len(frame) > 40:
        report.add("more than 40 rows")
    _check_ids(report, list(frame["trajectory_id"]))


def validate(stage: str, path: Path, shard: Path | None = None) -> Report:
    report = Report(stage=stage, path=str(path))
    if not path.exists():
        report.add("file does not exist")
        return report
    if stage == "normalized":
        validate_normalized(report, path)
    elif stage == "a":
        validate_stage_a(report, path)
    elif stage == "b":
        validate_jsonl(report, path, StageBResult)
    elif stage == "c1":
        validate_jsonl(report, path, StageC1Result)
    elif stage == "c2":
        outputs = validate_jsonl(report, path, StageC2Output)
        if shard is not None:
            check_c2_against_shard(
                report, [o for o in outputs if isinstance(o, StageC2Output)], shard
            )
    elif stage == "c2_input":
        validate_jsonl(report, path, C2Input)
    elif stage == "c2_merged":
        validate_jsonl(report, path, StageC2Result)
    elif stage == "ranked":
        validate_ranked(report, path)
    else:
        report.add(f"unknown stage {stage}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    parser.add_argument("path", type=Path)
    parser.add_argument("--shard", type=Path, default=None)
    args = parser.parse_args(argv)
    report = validate(args.stage, args.path, args.shard)
    print(f"{report.stage} {report.path}: {report.records} records, {len(report.errors)} errors")
    for error in report.errors:
        print(f"  {error}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
