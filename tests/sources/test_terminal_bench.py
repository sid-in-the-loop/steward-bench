from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from steward.config import Paths, Settings
from steward.schema import Trajectory
from steward.sources import terminal_bench
from steward.sources.terminal_bench import TerminalBench, allocate, trial_from_member

FIXTURES = Path(__file__).parent.parent / "fixtures" / "terminal_bench"
PREFIX = "submissions/terminal-bench/2.0"


def pack(submission_dir: Path, target: Path) -> None:
    """Mimic `git archive | gzip`: members in sorted path order under the repo prefix."""
    with tarfile.open(target, "w:gz") as archive:
        for path in sorted(submission_dir.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(FIXTURES)))


def make_settings(tmp_path: Path) -> Settings:
    archives = tmp_path / "raw" / "terminal_bench" / "archives"
    archives.mkdir(parents=True)
    for submission_dir in sorted((FIXTURES / PREFIX).iterdir()):
        pack(submission_dir, archives / f"{submission_dir.name}.tar.gz")
    return Settings(paths=Paths(root=Path(__file__).parents[2], data=tmp_path))


@pytest.fixture
def loaded(tmp_path: Path) -> dict[str, Trajectory]:
    return {t.trajectory_id: t for t in TerminalBench().load(make_settings(tmp_path))}


def test_only_atif_trials_load(loaded: dict[str, Trajectory]) -> None:
    assert sorted(loaded) == [
        "terminal_bench:CodeBrain-1__Model-B/2026-02-02__00-00-00/fix-git__xyz000",
        "terminal_bench:Terminus2__Model-A/2026-01-01__00-00-00/build-cython__def456",
        "terminal_bench:Terminus2__Model-A/2026-01-01__00-00-00/fix-git__abc123",
    ]


def test_standard_atif_trial(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["terminal_bench:Terminus2__Model-A/2026-01-01__00-00-00/fix-git__abc123"]
    assert [s.kind for s in trajectory.steps] == [
        "user",
        "agent_text",
        "action",
        "action",
        "result",
        "result",
        "agent_text",
        "action",
    ]
    assert trajectory.steps[2].tool == "bash_command"
    assert trajectory.steps[2].content == "git status\n"
    assert trajectory.steps[0].timestamp is not None
    assert all("hidden reasoning" not in s.content for s in trajectory.steps)
    assert trajectory.history_author == "model-a"
    assert trajectory.harness == "terminus-2"
    assert trajectory.task_env == "terminal-bench-2:fix-git"
    assert trajectory.domain == "terminal_devops"
    assert trajectory.real_user is False
    assert trajectory.meta["reward"] == 1.0
    assert trajectory.meta["task_git_commit"] == "deadbeef"
    assert trajectory.meta["agent_display_name"] == "Terminus 2"


def test_no_secrets_in_meta(loaded: dict[str, Trajectory]) -> None:
    for trajectory in loaded.values():
        assert "sk-fixture-not-real" not in repr(trajectory.meta)
        assert not any("key" in field or "token" in field for field in trajectory.meta)


def test_exception_type_kept(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded[
        "terminal_bench:Terminus2__Model-A/2026-01-01__00-00-00/build-cython__def456"
    ]
    assert trajectory.meta["exception_type"] == "AgentTimeoutError"


def test_codebrain_wrapper_variant(loaded: dict[str, Trajectory]) -> None:
    trajectory = loaded["terminal_bench:CodeBrain-1__Model-B/2026-02-02__00-00-00/fix-git__xyz000"]
    assert [s.kind for s in trajectory.steps] == [
        "user",
        "agent_text",
        "action",
        "result",
        "system",
    ]
    assert trajectory.steps[2].tool == "terminal"
    assert trajectory.steps[2].content == "git status"
    assert trajectory.history_author == "openai/model-b"
    assert trajectory.harness == "ruley"


def test_sampling_is_capped_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(terminal_bench, "MAX_TRIALS", 2)
    settings = make_settings(tmp_path)
    first = [t.trajectory_id for t in TerminalBench().load(settings)]
    second = [t.trajectory_id for t in TerminalBench().load(settings)]
    assert len(first) == 2
    assert first == second
    assert len({tid.split(":")[1].split("/")[0] for tid in first}) == 2


def test_allocate_spreads_evenly() -> None:
    assert allocate({"a": 1, "b": 10, "c": 10}, 9) == {"a": 1, "b": 4, "c": 4}
    assert allocate({"a": 1, "b": 2}, 10) == {"a": 1, "b": 2}


def test_trial_from_member() -> None:
    ref = trial_from_member(f"{PREFIX}/Sub__M/job1/task__x1/agent/trajectory.json")
    assert ref is not None
    assert ref.native_id == "Sub__M/job1/task__x1"
    assert trial_from_member(f"{PREFIX}/Sub__M/job1/task__x1/result.json") is None
