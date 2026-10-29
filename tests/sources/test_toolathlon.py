import shutil
from pathlib import Path

from steward.config import Settings
from steward.sources.toolathlon import Toolathlon, model_name

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "toolathlon"


def test_load(settings: Settings) -> None:
    shutil.copytree(FIXTURE, settings.paths.raw / "toolathlon")
    trajectories = list(Toolathlon().load(settings))
    assert [t.trajectory_id for t in trajectories] == ["toolathlon:model-a_1/weekly-report"]
    trajectory = trajectories[0]
    assert trajectory.domain == "workplace"
    assert trajectory.task_env == "toolathlon:weekly-report"
    assert trajectory.history_author == "model-a"
    assert trajectory.meta["passed"] is True
    assert [s.kind for s in trajectory.steps] == [
        "user",
        "agent_text",
        "action",
        "result",
        "action",
        "result",
        "agent_text",
    ]
    assert trajectory.steps[4].tool == "emails-send_email"
    assert "fixture-session-key" not in trajectory.model_dump_json()


def test_model_name() -> None:
    assert model_name("claude-4.5-opus_2") == "claude-4.5-opus"
    assert model_name("gpt-5") == "gpt-5"
