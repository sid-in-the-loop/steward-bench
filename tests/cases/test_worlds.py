"""The recorded sessions the Workplace tasks start from are kept in the repository."""

from pathlib import Path

from steward.config import Paths, Settings
from steward.prepare import recorded_session
from steward.task import World, case_directories, load_case

ROOT = Path(__file__).parents[2]


def test_every_workplace_task_finds_its_recorded_session() -> None:
    settings = Settings(paths=Paths(root=ROOT, data=ROOT / "unused"))
    worlds = [
        case.environment
        for case in map(load_case, case_directories(ROOT / "cases"))
        if isinstance(case.environment, World)
    ]
    assert worlds
    for world in worlds:
        session = recorded_session(settings, world.trajectory_id)
        assert session.trajectory_id == world.trajectory_id
        assert 0 < world.takeover_step <= len(session.steps)
        assert session.meta["task_name"]
