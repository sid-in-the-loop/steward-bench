"""Validate every stage output that exists."""

from __future__ import annotations

from steward.config import Settings
from steward.filter.validate import Report, validate
from steward.schema import AXES
from steward.store import source_dir, written_sources


def validate_all(settings: Settings) -> list[Report]:
    paths = settings.paths
    reports = [
        validate("normalized", source_dir(paths.normalized, s))
        for s in written_sources(paths.normalized)
    ]
    reports += [validate("a", p) for p in sorted(paths.stage_a.glob("*.parquet"))]
    for stage, path in (
        ("b", paths.stage_b / "results.jsonl"),
        ("c1", paths.stage_c1 / "results.jsonl"),
        ("c2_merged", paths.stage_c2 / "results.jsonl"),
    ):
        if path.exists():
            reports.append(validate(stage, path))
    reports += [validate("c2_input", p) for p in sorted(paths.handoff_c2.glob("shard_*.jsonl"))]
    for path in sorted(paths.ranked.glob("*/*.csv")):
        reports.append(validate("ranked" if path.stem in AXES else "final", path))
    return reports
