"""SWE-chat: real developer sessions with coding agents (HF SALT-NLP/SWE-chat, gated)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.base import Source, SourceInfo
from steward.sources.domain_rule import assign_domain
from steward.sources.hf import download_hf
from steward.sources.transcripts import parse_records, read_jsonl_records

LABEL_COLUMNS = (
    "agent",
    "model",
    "kind",
    "strategy",
    "user_persona",
    "session_success",
    "agent_percentage",
    "prompt_count",
    "turn_count",
    "duration_seconds",
    "files_touched_count",
    "summary_intent",
    "summary_outcome",
    "created_at",
    "branch",
    "cli_version",
)

HARNESS_BY_AGENT = {
    "Claude Code": "claude-code",
    "claude-code": "claude-code",
    "Codex": "codex",
    "Codex CLI": "codex",
    "OpenCode": "opencode",
    "opencode": "opencode",
    "Pi": "pi",
    "Pi Coding Agent": "pi",
    "Oh My Pi": "pi",
    "Copilot CLI": "copilot-cli",
    "Gemini CLI": "gemini-cli",
    "Cursor": "cursor",
}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def transcript_file(raw: Path, session_id: str, transcript_path: str | None) -> Path | None:
    candidates = [
        raw / "transcripts" / f"{session_id}.jsonl",
        raw / "transcripts1" / f"{session_id}.jsonl",
    ]
    if transcript_path:
        candidates.insert(0, raw / transcript_path)
    return next((path for path in candidates if path.is_file()), None)


def repo_lookup(raw: Path) -> dict[str, dict[str, Any]]:
    path = raw / "repositories.parquet"
    if not path.exists():
        return {}
    frame = pd.read_parquet(path, columns=["repo_id", "url", "license_type", "is_fork"])
    return {
        str(record["repo_id"]): {str(k): v for k, v in record.items()}
        for record in frame.to_dict("records")
    }


def checkpoint_commits(raw: Path) -> dict[str, list[str]]:
    """Commits linked to each checkpoint; the first one's parent is the session's start state."""
    path = raw / "checkpoints.parquet"
    if not path.exists():
        return {}
    frame = pd.read_parquet(path, columns=["checkpoint_pk", "commit_shas"])
    lookup: dict[str, list[str]] = {}
    for pk, shas in zip(frame["checkpoint_pk"], frame["commit_shas"], strict=True):
        try:
            values = json.loads(shas) if shas else []
        except (TypeError, json.JSONDecodeError):
            values = []
        lookup[str(pk)] = [str(v) for v in values if isinstance(v, str) and len(v) == 40]
    return lookup


def github_slug(url: str | None) -> str | None:
    if not url or "github.com" not in url:
        return None
    tail = url.split("github.com", 1)[1].strip("/:").removesuffix(".git")
    parts = tail.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


class SweChat(Source):
    info = SourceInfo(
        name="swe_chat",
        domain="swe",
        real_user=True,
        location="hf:SALT-NLP/SWE-chat",
        license="odc-by",
        notes="Claude Code, Codex, Pi and Copilot CLI transcripts are parsed; other agent "
        "formats (OpenCode, Cursor, Gemini CLI and smaller ones) and sessions flagged "
        "compact_transcript_delta_only are skipped",
    )

    def download(self, settings: Settings) -> None:
        download_hf(
            "SALT-NLP/SWE-chat",
            self.raw_dir(settings),
            [
                "README.md",
                "sessions.parquet",
                "session_logs.parquet",
                "repositories.parquet",
                "checkpoints.parquet",
                "transcripts/*",
                "transcripts1/*",
            ],
        )

    def load(self, settings: Settings) -> Iterator[Trajectory]:
        raw = self.raw_dir(settings)
        sessions = pd.read_parquet(raw / "sessions.parquet")
        repos = repo_lookup(raw)
        commits = checkpoint_commits(raw)
        for row in sessions.sort_values("session_id").to_dict("records"):
            if row.get("compact_transcript_delta_only"):
                continue
            session_id = str(row["session_id"])
            path = transcript_file(raw, session_id, row.get("transcript_path") or None)
            if path is None:
                continue
            fmt, parsed = parse_records(read_jsonl_records(str(path)))
            if parsed is None or not parsed.steps:
                continue
            repo_info = repos.get(str(row.get("repo_id")), {})
            repo = github_slug(repo_info.get("url")) or github_slug(parsed.repository_url)
            model = _clean(row.get("model")) or None
            if not model and parsed.models:
                model = parsed.models.most_common(1)[0][0]
            session_commits = commits.get(str(row.get("canonical_checkpoint_pk")), [])
            domain = assign_domain(parsed.steps)
            meta = {column: _clean(row.get(column)) for column in LABEL_COLUMNS}
            meta.update(
                {
                    "transcript_format": fmt,
                    "repo_id": row.get("repo_id"),
                    "repo_license": repo_info.get("license_type"),
                    "is_continuation": parsed.is_continuation,
                    "models_seen": dict(parsed.models),
                    "session_commits": session_commits,
                }
            )
            yield Trajectory(
                trajectory_id=f"swe_chat:{session_id}",
                source="swe_chat",
                domain=domain or "swe",
                domain_decided_by_rule=domain is not None,
                real_user=True,
                history_author=model,
                harness=HARNESS_BY_AGENT.get(str(row.get("agent")), str(row.get("agent") or ""))
                or None,
                repo=repo,
                base_commit=parsed.base_commit or (session_commits[0] if session_commits else None),
                task_env=None,
                license=self.info.license,
                steps=parsed.steps,
                meta=json.loads(json.dumps(meta, default=str)),
            )
