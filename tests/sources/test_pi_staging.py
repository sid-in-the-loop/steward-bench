import shutil
from pathlib import Path

from steward.config import Settings
from steward.sources.pi_staging import PiStaging, card_repo

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "pi_staging"


def test_load_pi_sessions(settings: Settings) -> None:
    shutil.copytree(FIXTURE, settings.paths.raw / "pi_staging")
    trajectories = list(PiStaging().load(settings))
    assert [t.trajectory_id for t in trajectories] == ["pi_staging:0000-aaaa"]
    trajectory = trajectories[0]
    assert [s.kind for s in trajectory.steps] == [
        "user",
        "agent_text",
        "action",
        "result",
        "system",
        "result",
    ]
    assert trajectory.steps[2].tool == "bash"
    assert trajectory.steps[2].content == "rg helper"
    assert trajectory.steps[4].is_interruption
    assert not any("hidden plan" in s.content for s in trajectory.steps)
    assert trajectory.history_author == "claude-opus-4-6"
    assert trajectory.harness == "pi"
    assert trajectory.repo == "alice/pi-demo"
    assert trajectory.license == "other"
    assert trajectory.real_user
    assert trajectory.meta["dataset"] == "alice/pi-demo"


def test_card_repo_from_source_line(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("Source git repo: https://github.com/badlogic/pi-diff-review.git\n")
    assert card_repo(readme) == "badlogic/pi-diff-review"
