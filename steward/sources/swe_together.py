"""SWE-Together: the real user sessions behind each SWE-Together task (GitHub Togetherbench/SWE-Together).

Each task directory holds original_session.json, the human-agent session the task was cut from,
in one of three shapes: Anthropic content blocks, DataClaw-style flat messages, or plain chat
with <tool_use> markup (the hyperswitch-derived tasks). Sessions were taken from pi_staging,
DataClaw, SWE-chat and hyperswitch, so upstream ids are kept in meta for cross-source dedup.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tomllib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Step, Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.domain_rule import assign_domain
from steward.sources.hf import download_hf
from steward.sources.session_export_formats import (
    clean_model,
    has_tool_markup,
    infer_harness,
    parse_markup_messages,
    parse_session_messages,
)
from steward.sources.swe_chat import github_slug

log = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/Togetherbench/SWE-Together.git"
TASKS_REPO = "yifannnwu/SWE-Together"
REPO_DIR = "repo"
TASKS_DIR = "hf_yifannnwu__SWE-Together"

PROVENANCE_FIELDS = {
    "session_id": "upstream_session_id",
    "_source": "upstream_dataset",
    "_hf_repo": "upstream_hf_repo",
    "_donor": "upstream_donor",
    "_github_repo": "upstream_github_repo",
    "_source_file": "upstream_source_file",
    "source": "upstream_harness",
    "project": "project",
    "git_branch": "git_branch",
    "start_time": "start_time",
    "end_time": "end_time",
}
TASK_FIELDS = ("language", "difficulty", "category", "scoring_tier", "docker_image")
ORACLE_FIELDS = ("_session_id", "_source", "_canonical_source_path", "repo_id")


class TaskSpec(BaseModel):
    """Task metadata joined onto a session: from the HF rows, task.toml and the oracle header."""

    repo: str | None = None
    base_commit: str | None = None
    fields: dict[str, Any] = {}


def task_env(task_id: str) -> str:
    return f"swe-together:{task_id}"


def read_task_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return rows
    with path.open() as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[str(row["task_id"])] = row
    return rows


def oracle_header(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "oracle_session.jsonl"
    if not path.is_file():
        return {}
    with path.open() as handle:
        first = handle.readline()
    try:
        header = json.loads(first)
    except json.JSONDecodeError:
        return {}
    return header if isinstance(header, dict) and header.get("_is_header") else {}


def toml_metadata(task_dir: Path) -> dict[str, Any]:
    path = task_dir / "task.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return {}
    metadata = data.get("metadata") or {}
    return {
        key: metadata.get(key)
        for key in ("session_resolution", "session_resolution_confidence")
        if key in metadata
    }


def task_spec(task_dir: Path, row: dict[str, Any]) -> TaskSpec:
    header = oracle_header(task_dir)
    repo = (
        row.get("repo") or github_slug(row.get("repo_url")) or github_slug(header.get("_repo_url"))
    )
    fields = {key: row.get(key) for key in TASK_FIELDS if key in row}
    fields.update(
        {f"oracle_{key.lstrip('_')}": header.get(key) for key in ORACLE_FIELDS if key in header}
    )
    fields.update(toml_metadata(task_dir))
    return TaskSpec(
        repo=repo or None,
        base_commit=row.get("base_commit") or header.get("_base_commit") or None,
        fields=fields,
    )


def message_shape(messages: list[dict[str, Any]]) -> str:
    if has_tool_markup(messages) and not any("timestamp" in m for m in messages):
        return "chat_markup"
    if any(isinstance(m.get("content"), list) for m in messages):
        return "content_blocks"
    return "flat"


def session_steps(messages: list[dict[str, Any]], shape: str) -> list[Step]:
    if shape == "chat_markup":
        return parse_markup_messages(messages)
    return parse_session_messages(messages)


def session_harness(session: dict[str, Any], steps: list[Step]) -> str | None:
    dataset_name = str(session.get("_hf_repo") or "").split("/")[-1].replace("_", "-")
    if dataset_name.startswith("pi-"):
        return "pi"
    if session.get("_source") == "archit11/claude_traces_hs":
        return "claude-code"
    source = session.get("source")
    return infer_harness(source if isinstance(source, str) else None, steps)


class SweTogether(Source):
    info = SourceInfo(
        name="swe_together",
        domain="swe",
        real_user=True,
        location="github:Togetherbench/SWE-Together, hf:yifannnwu/SWE-Together",
        license="apache-2.0",
        notes="Upstream sessions overlap pi_staging, dataclaw, swe_chat and hyperswitch; see meta.",
    )

    def download(self, settings: Settings) -> None:
        raw = self.raw_dir(settings)
        raw.mkdir(parents=True, exist_ok=True)
        if not (raw / REPO_DIR / ".git").is_dir():
            subprocess.run(
                ["git", "clone", "--depth", "1", GITHUB_URL, str(raw / REPO_DIR)], check=True
            )
        download_hf(TASKS_REPO, raw / TASKS_DIR)

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        raw = self.raw_dir(settings)
        rows = read_task_rows(raw / TASKS_DIR / "swe_together.jsonl")
        skipped: Counter[str] = Counter()
        tasks = raw / REPO_DIR / "tasks"
        for task_dir in sorted(p for p in tasks.iterdir() if p.is_dir()):
            path = task_dir / "original_session.json"
            if not path.is_file():
                continue
            try:
                session = json.loads(path.read_text())
            except json.JSONDecodeError:
                skipped["unparsable"] += 1
                continue
            messages = [m for m in session.get("messages") or [] if isinstance(m, dict)]
            shape = message_shape(messages)
            steps = session_steps(messages, shape)
            if not steps:
                skipped["no_steps"] += 1
                continue
            task_id = task_dir.name
            spec = task_spec(task_dir, rows.get(task_id, {}))
            domain = assign_domain(steps)
            meta: dict[str, Any] = {
                "task_id": task_id,
                "message_shape": shape,
                "model": session.get("model"),
            }
            meta.update(
                {
                    name: session[field]
                    for field, name in PROVENANCE_FIELDS.items()
                    if field in session
                }
            )
            meta.update(spec.fields)
            yield Trajectory(
                trajectory_id=f"swe_together:{task_id}",
                source="swe_together",
                domain=domain or "swe",
                domain_decided_by_rule=domain is not None,
                real_user=True,
                history_author=clean_model(session.get("model")),
                harness=session_harness(session, steps),
                repo=spec.repo,
                base_commit=spec.base_commit,
                task_env=task_env(task_id),
                license=self.info.license,
                steps=steps,
                meta=json.loads(json.dumps(meta, default=str)),
            )
        log.info("swe_together skipped: %s", dict(skipped))
