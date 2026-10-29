import shutil
from pathlib import Path

import pytest

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.swe_together import SweTogether
from steward.sources.swe_together_kimi import SweTogetherKimi

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "swe_together"


@pytest.fixture
def placed(settings: Settings) -> Settings:
    shutil.copytree(FIXTURE, settings.paths.raw / "swe_together")
    return settings


@pytest.fixture
def loaded(placed: Settings) -> dict[str, Trajectory]:
    return {t.trajectory_id: t for t in SweTogether().load(placed)}


def test_ids_and_task_env(loaded: dict[str, Trajectory]) -> None:
    assert list(loaded) == [
        "swe_together:blocks-task-1",
        "swe_together:flat-task-2",
        "swe_together:markup-task-3",
    ]
    for task_id, trajectory in loaded.items():
        assert trajectory.task_env == "swe-together:" + task_id.split(":", 1)[1]
        assert trajectory.real_user
        assert trajectory.license == "apache-2.0"
        assert not any("secret plan" in s.content for s in trajectory.steps)


def test_content_blocks_session(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["swe_together:blocks-task-1"]
    assert [s.kind for s in trajectory.steps] == ["user", "action", "result", "user", "agent_text"]
    assert trajectory.steps[1].tool == "bash"
    assert trajectory.steps[1].content == "npm run build"
    assert trajectory.steps[3].is_interruption
    assert trajectory.repo == "alice/blocks"
    assert trajectory.base_commit == "a" * 40
    assert trajectory.harness == "pi"
    assert trajectory.history_author == "claude-opus-4-6"
    assert trajectory.meta["message_shape"] == "content_blocks"
    assert trajectory.meta["upstream_session_id"] == "pi-upstream-uuid"
    assert trajectory.meta["upstream_hf_repo"] == "badlogicgames/pi_mono"
    assert trajectory.meta["session_resolution"] == "resolved"
    assert "reference_patch" not in trajectory.meta


def test_flat_session_uses_oracle_header(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["swe_together:flat-task-2"]
    assert [s.kind for s in trajectory.steps] == ["user", "action", "result"]
    assert trajectory.steps[1].content == "git diff"
    assert trajectory.steps[2].content == "diff --git a b"
    assert trajectory.repo == "bob/flat"
    assert trajectory.base_commit == "b" * 40
    assert trajectory.harness == "codex"
    assert trajectory.history_author is None
    assert trajectory.meta["message_shape"] == "flat"
    assert trajectory.meta["oracle_session_id"] == "dc-upstream-id"


def test_markup_session(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["swe_together:markup-task-3"]
    assert [s.kind for s in trajectory.steps] == ["user", "agent_text", "action", "result"]
    assert trajectory.steps[2].tool == "Read"
    assert trajectory.harness == "claude-code"
    assert trajectory.meta["message_shape"] == "chat_markup"
    assert trajectory.meta["upstream_dataset"] == "archit11/claude_traces_hs"


def test_kimi_session_rebuilt_from_requests(placed: Settings) -> None:
    trajectories = list(SweTogetherKimi().load(placed))
    assert [t.trajectory_id for t in trajectories] == ["swe_together_kimi:kimi-s1"]
    trajectory = trajectories[0]
    assert [s.kind for s in trajectory.steps] == [
        "system",
        "user",
        "action",
        "result",
        "agent_text",
        "action",
        "result",
    ]
    assert [s.tool for s in trajectory.steps if s.kind == "action"] == ["Shell", "WriteFile"]
    assert trajectory.steps[2].content == "ls"
    assert trajectory.history_author == "Kimi-K2.6"
    assert trajectory.harness == "kimi-code"
    assert not trajectory.real_user
    assert trajectory.task_env == "swe-together:blocks-task-1"
    assert trajectory.repo == "alice/blocks"
    assert trajectory.meta["requests"] == 3
