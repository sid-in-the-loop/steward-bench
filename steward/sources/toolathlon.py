"""Toolathlon agent runs (HF hkust-nlp/Toolathlon-Trajectories).

Each file is one model run (`<model>_<run>.jsonl`) with one row per task. Agents work across
real application servers (email, Notion, Canvas, spreadsheets, cloud consoles), so the source is
filed under workplace. The row's `config` holds launch settings including a session token key,
so it is never copied into meta.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.chat_messages import parse_openai_messages
from steward.sources.hf import download_hf

log = logging.getLogger(__name__)

HF_REPO = "hkust-nlp/Toolathlon-Trajectories"
HARNESS = "toolathlon"
RUN_SUFFIX = re.compile(r"_\d+$")

Row = dict[str, Any]


def decode(value: Any) -> Any:
    """Row fields are JSON strings; return the decoded value, or None when absent or invalid."""
    if not isinstance(value, str):
        return value
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def model_name(run: str) -> str:
    return RUN_SUFFIX.sub("", run)


def iter_rows(path: Path) -> Iterator[Row]:
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("task_name") and row.get("modelname_run"):
                yield row


def to_trajectory(row: Row, license_: str | None) -> Trajectory | None:
    messages = decode(row.get("messages"))
    if not isinstance(messages, list):
        return None
    steps = parse_openai_messages(m for m in messages if isinstance(m, dict))
    if not steps:
        return None
    run = str(row["modelname_run"])
    task = str(row["task_name"])
    status = decode(row.get("task_status")) or {}
    return Trajectory(
        trajectory_id=f"toolathlon:{run}/{task}",
        source="toolathlon",
        domain="workplace",
        real_user=False,
        history_author=model_name(run),
        harness=HARNESS,
        task_env=f"toolathlon:{task}",
        license=license_,
        steps=steps,
        meta={
            "run": run,
            "task_name": task,
            "passed": status.get("evaluation") if isinstance(status, dict) else None,
            "key_stats": decode(row.get("key_stats")),
            "agent_cost": decode(row.get("agent_cost")),
            "started": row.get("initial_run_time"),
            "completed": row.get("completion_time"),
        },
    )


class Toolathlon(Source):
    info = SourceInfo(
        name="toolathlon",
        domain="workplace",
        real_user=False,
        location=f"hf:{HF_REPO}",
        license="cc-by-4.0",
        notes="108 tasks across real application servers, one file per model run. Filed under "
        "workplace. Toolathlon-Verified_Trajectories is not used: its terms forbid "
        "redistribution, and C2 shards go to a public branch.",
    )

    def download(self, settings: Settings) -> None:
        download_hf(HF_REPO, settings.paths.raw / self.info.name)

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        for path in sorted((settings.paths.raw / self.info.name).glob("*.jsonl")):
            for row in iter_rows(path):
                trajectory = to_trajectory(row, self.info.license)
                if trajectory is not None:
                    yield trajectory
