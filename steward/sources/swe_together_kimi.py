"""Kimi-K2.6 agent runs on the SWE-Together tasks (HF verda-research/kimicode_swetogether_traces).

Rows are captured LLM requests, not sessions. The harness trims history between calls, so a
session is rebuilt by ordering its requests by time and appending every message not already seen.
The final assistant reply of each session is not in the capture.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.chat_messages import parse_openai_messages
from steward.sources.hf import download_hf
from steward.sources.swe_together import TASKS_DIR, read_task_rows, task_env

log = logging.getLogger(__name__)

HF_REPO = "verda-research/kimicode_swetogether_traces"
RAW_SUBDIR = ("swe_together", "hf_verda-research__kimicode_swetogether_traces")
TRACES_FILE = "traces_r3_canonical.jsonl"
HARNESS = "kimi-code"

Row = dict[str, Any]


def message_key(message: dict[str, Any]) -> str:
    return json.dumps(message, sort_keys=True, ensure_ascii=False, default=str)


def rebuild_session(rows: list[Row]) -> list[dict[str, Any]]:
    """The session's messages in order, merged from requests sorted by capture time."""
    messages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(
        rows, key=lambda r: (float(r.get("timestamp_offset_sec") or 0), str(r["id"]))
    ):
        for message in row.get("messages") or []:
            if not isinstance(message, dict):
                continue
            key = message_key(message)
            if key not in seen:
                seen.add(key)
                messages.append(message)
    return messages


def read_rows(path: Path) -> dict[str, list[Row]]:
    sessions: dict[str, list[Row]] = defaultdict(list)
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("session_id"):
                sessions[str(row["session_id"])].append(row)
    return sessions


class SweTogetherKimi(Source):
    info = SourceInfo(
        name="swe_together_kimi",
        domain="swe",
        real_user=False,
        location=f"hf:{HF_REPO}",
        license="apache-2.0",
        notes="Raw data lives under data/raw/swe_together; no verifier outcome is recorded.",
    )

    def raw_dir(self, settings: Settings) -> Path:
        return settings.paths.raw.joinpath(*RAW_SUBDIR)

    def download(self, settings: Settings) -> None:
        download_hf(HF_REPO, self.raw_dir(settings))

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        sessions = read_rows(self.raw_dir(settings) / TRACES_FILE)
        task_rows = read_task_rows(
            settings.paths.raw / RAW_SUBDIR[0] / TASKS_DIR / "swe_together.jsonl"
        )
        for session_id in sorted(sessions):
            rows = sessions[session_id]
            steps = parse_openai_messages(rebuild_session(rows))
            if not steps:
                continue
            first = rows[0]
            task = str(first.get("task") or "")
            models = sorted({str(r["model"]) for r in rows if r.get("model")})
            task_row = task_rows.get(task, {})
            yield Trajectory(
                trajectory_id=f"swe_together_kimi:{session_id}",
                source="swe_together_kimi",
                domain="swe",
                real_user=False,
                history_author=models[0] if models else None,
                harness=HARNESS,
                repo=task_row.get("repo") or None,
                base_commit=task_row.get("base_commit") or None,
                task_env=task_env(task) if task else None,
                license=self.info.license,
                steps=steps,
                meta={
                    "task_id": task,
                    "schema": first.get("schema"),
                    "requests": len(rows),
                    "models": models,
                    "user_agent": first.get("metadata", {}).get("user_agent"),
                    "final_reply_captured": False,
                },
            )
