"""Tests for the SWE-chat, Hyperswitch and Nebius OpenHands loaders on synthetic fixtures."""

import json

import pandas as pd

from steward.sources.hyperswitch import Hyperswitch
from steward.sources.nebius_openhands import NebiusOpenHands
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


def test_hyperswitch(settings) -> None:
    raw = settings.paths.raw / "hyperswitch" / "data"
    raw.mkdir(parents=True)
    chatml = (
        "<|im_start|>user\nImplement the change<|im_end|>"
        '<|im_start|>assistant\n<think>x</think><tool_use id="1">{"name": "Edit", "arguments": {"file_path": "a.rs"}}</tool_use><|im_end|>'
        '<|im_start|>user\n<tool_result id="1">done</tool_result><|im_end|>'
    )
    pd.DataFrame(
        {
            "parent_dir": ["logs_glm"],
            "prefix": ["juspay__hyperswitch-1"],
            "chatml": [chatml],
            "gitdiff": [""],
        }
    ).to_parquet(raw / "train.parquet")
    [trajectory] = list(Hyperswitch().load(settings))
    assert trajectory.trajectory_id == "hyperswitch:logs_glm/juspay__hyperswitch-1"
    assert trajectory.task_env == "juspay__hyperswitch-1" and trajectory.history_author == "glm"
    assert [s.kind for s in trajectory.steps] == ["user", "action", "result"]
    assert not trajectory.real_user


def test_nebius_sampling_is_deterministic(settings) -> None:
    raw = settings.paths.raw / "nebius_openhands"
    raw.mkdir(parents=True)
    message = [
        {"role": "user", "content": "fix", "name": None, "tool_call_id": None, "tool_calls": None}
    ]
    pd.DataFrame(
        {
            "trajectory_id": [f"t{i}" for i in range(10)],
            "instance_id": [f"o__r-{i}" for i in range(10)],
            "repo": ["o/r"] * 10,
            "trajectory": [message] * 10,
            "exit_status": ["submitted"] * 10,
            "resolved": [1] * 10,
        }
    ).to_parquet(raw / "trajectories.parquet")
    settings.sampling.nebius_sample = 4
    first = [t.trajectory_id for t in NebiusOpenHands().load(settings)]
    second = [t.trajectory_id for t in NebiusOpenHands().load(settings)]
    assert first == second and len(first) == 4
    assert all(t.startswith("nebius_openhands:t") for t in first)
