"""Load every stage output, and compute per-trajectory step statistics in one streaming pass."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from steward.config import Settings
from steward.filter.render import CHARS_PER_TOKEN
from steward.filter.stage_a import read_results as read_stage_a
from steward.schema import AXES, StageAResult, StageBResult, StageC1Result, StageC2Result
from steward.store import open_steps, read_jsonl, read_meta, source_dir, written_sources

log = logging.getLogger(__name__)


class AnalysisInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    meta: pd.DataFrame
    stage_a: list[StageAResult]
    stage_b: list[StageBResult]
    stage_c1: list[StageC1Result]
    stage_c2: list[StageC2Result]
    stats: pd.DataFrame
    interruptions: pd.DataFrame


def _first_word(command: str) -> str:
    words = command.strip().split()
    return words[0][:40] if words else ""


def step_statistics(settings: Settings, source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tokens, duration and user turns per trajectory; one row per interruption event."""
    cache = settings.paths.data / "analysis" / source
    stats_path, events_path = cache / "stats.parquet", cache / "interruptions.parquet"
    done = source_dir(settings.paths.normalized, source) / "_DONE"
    if stats_path.exists() and stats_path.stat().st_mtime >= done.stat().st_mtime:
        return pd.read_parquet(stats_path), pd.read_parquet(events_path)
    stats: dict[str, dict[str, object]] = {}
    events: list[dict[str, object]] = []
    last_action: dict[str, tuple[str, str]] = {}
    with open_steps(source_dir(settings.paths.normalized, source)) as handle:
        for line in handle:
            record = json.loads(line)
            trajectory_id = record["trajectory_id"]
            row = stats.setdefault(
                trajectory_id,
                {
                    "trajectory_id": trajectory_id,
                    "chars": 0,
                    "first": None,
                    "last": None,
                    "steps": 0,
                },
            )
            row["chars"] = int(row["chars"]) + len(record["content"])  # type: ignore[call-overload]
            row["steps"] = int(row["steps"]) + 1  # type: ignore[call-overload]
            if record.get("timestamp"):
                stamp = datetime.fromisoformat(record["timestamp"])
                if row["first"] is None:
                    row["first"] = stamp
                row["last"] = stamp
            if record["kind"] == "action":
                last_action[trajectory_id] = (
                    str(record.get("tool") or ""),
                    _first_word(record["content"]),
                )
            if record.get("is_interruption"):
                tool, word = last_action.get(trajectory_id, ("", ""))
                events.append(
                    {
                        "trajectory_id": trajectory_id,
                        "step": record["index"],
                        "prev_tool": tool,
                        "prev_command": word,
                    }
                )
    rows = []
    for row in stats.values():
        first, last = row["first"], row["last"]
        duration = (
            (last - first).total_seconds()
            if isinstance(first, datetime) and isinstance(last, datetime)
            else None
        )
        rows.append(
            {
                "trajectory_id": row["trajectory_id"],
                "tokens": int(int(row["chars"]) / CHARS_PER_TOKEN),  # type: ignore[call-overload]
                "duration_seconds": duration,
                "steps": row["steps"],
            }
        )
    stats_frame = pd.DataFrame(
        rows, columns=["trajectory_id", "tokens", "duration_seconds", "steps"]
    )
    events_frame = pd.DataFrame(
        events, columns=["trajectory_id", "step", "prev_tool", "prev_command"]
    )
    cache.mkdir(parents=True, exist_ok=True)
    stats_frame.to_parquet(stats_path, index=False)
    events_frame.to_parquet(events_path, index=False)
    return stats_frame, events_frame


def load(settings: Settings) -> AnalysisInputs:
    sources = written_sources(settings.paths.normalized)
    meta = pd.DataFrame(
        [m.model_dump() for s in sources for m in read_meta(settings.paths.normalized, s)]
    )
    stats_parts, event_parts = [], []
    for source in sources:
        stats, events = step_statistics(settings, source)
        stats_parts.append(stats)
        event_parts.append(events)
    c2_path = settings.paths.stage_c2 / "results.jsonl"
    return AnalysisInputs(
        meta=meta,
        stage_a=read_stage_a(settings),
        stage_b=read_jsonl(settings.paths.stage_b / "results.jsonl", StageBResult),
        stage_c1=read_jsonl(settings.paths.stage_c1 / "results.jsonl", StageC1Result),
        stage_c2=read_jsonl(c2_path, StageC2Result),
        stats=pd.concat(stats_parts, ignore_index=True) if stats_parts else pd.DataFrame(),
        interruptions=pd.concat(event_parts, ignore_index=True) if event_parts else pd.DataFrame(),
    )


def ranked_files(settings: Settings) -> list[Path]:
    return sorted(p for p in settings.paths.ranked.glob("*/*.csv") if p.stem in AXES)
