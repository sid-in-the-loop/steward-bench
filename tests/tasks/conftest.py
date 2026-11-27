"""The task and session the session tests run on."""

from pathlib import Path

import pytest

from steward.changes import AuthorityConflict, GoalChange, GoalStep
from steward.session import SessionSpec, load_session_spec
from steward.task import Reference, Task, load_task

ROOT = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def task() -> Task:
    return load_task(ROOT / "swe-082dc18eaa")


@pytest.fixture(scope="session")
def spec(task: Task) -> SessionSpec:
    loaded = load_session_spec(ROOT / "swe-082dc18eaa", task)
    assert loaded is not None
    return loaded


NOTHING = Reference(actions=[], report="done")


@pytest.fixture(scope="session")
def full(spec: SessionSpec) -> SessionSpec:
    """The session with a goal change of one step and an authority conflict, so that it runs
    under both conditions of the benchmark."""
    step = GoalStep(
        line="step", harm="true", done="true", safe_reference=NOTHING, harmful_reference=NOTHING
    )
    conflict = AuthorityConflict(
        first="owner",
        first_line="a",
        second="manager",
        second_line="b",
        harm="true",
        safe_reference=NOTHING,
        harmful_reference=NOTHING,
    )
    return spec.model_copy(
        update={"goal": GoalChange(steps=[step], final="true"), "conflict": conflict}
    )
