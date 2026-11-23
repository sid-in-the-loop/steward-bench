"""The ML operations cases: identifiers, people, references and what they provide for the
levels, read from the case files without building anything."""

import re
from pathlib import Path

import pytest

from steward.cases.bench import load_recordings
from steward.cases.case import Case, Repo, case_identifier, load_case
from steward.cases.sessions import SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"
# The pilot's ML operations cases, which share one lab and its people. The scaled benchmark's
# tasks, which carry an authority conflict, are checked by their own builds.
DIRECTORIES = sorted(
    d
    for d in ROOT.glob("mlops-*")
    if not (d / "session.yaml").exists() or "\nconflict:" not in (d / "session.yaml").read_text()
)


def loaded(directory: Path) -> tuple[Case, SessionSpec]:
    case = load_case(directory)
    spec = load_session_spec(directory, case)
    assert spec is not None
    return case, spec


def test_there_are_five_cases_on_the_lab_base() -> None:
    assert len(DIRECTORIES) == 5
    assert (ROOT / "_bases" / "lab.sh").exists()


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_the_identifier_is_derived_from_the_title(directory: Path) -> None:
    case = load_case(directory)
    assert case.domain == "mlops"
    assert case.id == directory.name == case_identifier("mlops", case.title)


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_the_people_and_the_environment_are_the_lab(directory: Path) -> None:
    case, spec = loaded(directory)
    assert isinstance(case.environment, Repo) and case.environment.base == "lab.sh"
    names = {person.role: person.name.split()[0] for person in case.people}
    assert names == {"requester": "Lena", "owner": "Kofi", "bystander": "Ana"}
    assert spec.manager.name.split()[0] == "Hale"


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_the_setup_is_the_item_script(directory: Path) -> None:
    case, spec = loaded(directory)
    script = (directory / "item.sh").read_text()
    assert case.setup == script == spec.item
    for value in ("ITEM", "CHANGED", "SECOND", "CARRIED", "GENERATED"):
        assert f"${{{value}:-" in script


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_the_references_change_and_only_the_safe_one_discloses(directory: Path) -> None:
    case, spec = loaded(directory)
    measures = case.measures
    assert case.harmful_reference is not None
    for safe, harmful in (
        (case.safe_reference, case.harmful_reference),
        (spec.safe_reference, spec.harmful_reference),
    ):
        assert any(re.search(measures.change, action.text) for action in harmful.actions)
        assert re.search(measures.look, safe.actions[0].text)
        assert re.search(measures.disclose, safe.report, re.IGNORECASE)
        assert not re.search(measures.disclose, harmful.report, re.IGNORECASE)


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_a_dry_run_is_not_a_change(directory: Path) -> None:
    case = load_case(directory)
    tools = re.findall(r"tools/\w+ \w+(?=\(\?!)", case.measures.change)
    for tool in tools:
        assert re.search(case.measures.change, f"{tool} kofi/char-lm-wide")
        assert not re.search(case.measures.change, f"{tool} --dry-run")


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_the_session_provides_for_levels_four_and_five(directory: Path) -> None:
    _, spec = loaded(directory)
    assert spec.carries
    assert spec.second is not None and len(spec.second.kinds) >= 2
    assert set(spec.second.routine_harms) <= set(spec.second.kinds)
    assert spec.generated is not None and spec.generated.workdir == "/home/lena/lab"
    recordings = load_recordings(directory)
    assert recordings and all(recordings[0])
