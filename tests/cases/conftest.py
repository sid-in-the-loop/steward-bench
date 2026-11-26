"""The case and session the session tests run on."""

from pathlib import Path

import pytest

from steward.cases.case import Case, Reference, load_case
from steward.cases.changes import AuthorityConflict, GoalChange, GoalStep
from steward.cases.sessions import SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"


@pytest.fixture(scope="session")
def case() -> Case:
    return load_case(ROOT / "swe-082dc18eaa")


@pytest.fixture(scope="session")
def spec(case: Case) -> SessionSpec:
    loaded = load_session_spec(ROOT / "swe-082dc18eaa", case)
    assert loaded is not None
    # The tests add what a case provides for Levels IV and V themselves.
    plain = {"levels": None, "carries": False, "second": None, "generated": None}
    return loaded.model_copy(update=plain)


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
