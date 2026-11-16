"""The case and session the session tests run on."""

from pathlib import Path

import pytest

from steward.cases.case import Case, load_case
from steward.cases.sessions import SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"


@pytest.fixture(scope="session")
def case() -> Case:
    return load_case(ROOT / "s1")


@pytest.fixture(scope="session")
def spec(case: Case) -> SessionSpec:
    loaded = load_session_spec(ROOT / "s1", case)
    assert loaded is not None
    return loaded
