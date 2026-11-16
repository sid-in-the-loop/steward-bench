"""The workplace session cases: what each provides for Levels IV and V, read without a world."""

from pathlib import Path

import pytest

from steward.cases.bench import load_recordings
from steward.cases.case import case_identifier, load_case
from steward.cases.sessions import SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"
NEW = ("workplace-d041b24e3c", "workplace-2b51cff6b1")
# Cases with stored Level I sessions, which were made on these versions; the Level IV and V
# fields must leave them as they are.
LEGACY_VERSIONS = {
    "workplace-55f9d68c1c": "760592200f",
    "workplace-6cc38f87f9": "8eb321bdb1",
    "workplace-bd5be465f4": "31a9299310",
}
WORKPLACE = (*NEW, *LEGACY_VERSIONS)


def spec_of(folder: str) -> SessionSpec:
    spec = load_session_spec(ROOT / folder, load_case(ROOT / folder))
    assert spec is not None
    return spec


@pytest.mark.parametrize("folder", NEW)
def test_a_new_case_is_named_by_its_domain_and_title(folder: str) -> None:
    case = load_case(ROOT / folder)
    assert case.domain == "workplace"
    assert case.id == folder == case_identifier("workplace", case.title)


@pytest.mark.parametrize("folder", list(LEGACY_VERSIONS))
def test_an_existing_case_keeps_its_session_version(folder: str) -> None:
    spec = spec_of(folder)
    assert spec.version == LEGACY_VERSIONS[folder]
    assert spec.levels is not None and spec.levels_version


@pytest.mark.parametrize("folder", WORKPLACE)
def test_a_workplace_case_provides_levels_four_and_five(folder: str) -> None:
    spec = spec_of(folder)
    assert spec.carries
    assert spec.second is not None and len(spec.second.kinds) >= 2
    assert set(spec.second.routine_harms) <= set(spec.second.kinds)
    assert spec.generated is not None
    assert spec.generated.workdir in (spec.levels or spec.item)
    recordings = load_recordings(ROOT / folder)
    assert recordings and all(isinstance(command, str) for command in recordings[0])
