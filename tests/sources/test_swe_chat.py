import json

import pandas as pd

from steward.sources.swe_chat import SweChat, github_slug


def write_jsonl(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_swe_chat(settings) -> None:
    raw = settings.paths.raw / "swe_chat"
    raw.mkdir(parents=True)
    columns = {
        "session_id": ["s1", "s2", "s3"],
        "repo_id": ["o/r", "o/r", "o/r"],
        "canonical_checkpoint_pk": ["o/r#1", "o/r#2", "o/r#3"],
        "agent": ["Claude Code", "Codex", "Claude Code"],
        "model": ["", "gpt-5.4", ""],
        "transcript_path": ["transcripts/2026-01-01-s1.jsonl", "", ""],
        "compact_transcript_delta_only": [False, False, True],
        "user_persona": ["Vague Requester", "Other", "Other"],
        "agent_percentage": [90.0, 50.0, 10.0],
    }
    pd.DataFrame(columns).to_parquet(raw / "sessions.parquet")
    pd.DataFrame(
        {
            "repo_id": ["o/r"],
            "url": ["https://github.com/o/r"],
            "license_type": ["MIT"],
            "is_fork": [False],
        }
    ).to_parquet(raw / "repositories.parquet")
    pd.DataFrame({"checkpoint_pk": ["o/r#1"], "commit_shas": [json.dumps(["b" * 40])]}).to_parquet(
        raw / "checkpoints.parquet"
    )
    write_jsonl(
        raw / "transcripts" / "s1.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "fix"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
                },
            },
        ],
    )
    write_jsonl(
        raw / "transcripts1" / "s2.jsonl",
        [
            {"type": "session_meta", "payload": {"git": {"commit_hash": "c" * 40}}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "go"}],
                },
            },
        ],
    )
    trajectories = {t.trajectory_id: t for t in SweChat().load(settings)}
    assert set(trajectories) == {"swe_chat:s1", "swe_chat:s2"}
    first = trajectories["swe_chat:s1"]
    assert first.history_author == "claude-opus-4-6" and first.harness == "claude-code"
    assert first.repo == "o/r" and first.base_commit == "b" * 40
    assert first.meta["user_persona"] == "Vague Requester"
    assert trajectories["swe_chat:s2"].base_commit == "c" * 40
    assert trajectories["swe_chat:s2"].harness == "codex"


def test_github_slug() -> None:
    assert github_slug("https://github.com/a/b.git") == "a/b"
    assert github_slug("git@github.com:a/b.git") == "a/b"
    assert github_slug("https://gitlab.com/a/b") is None
