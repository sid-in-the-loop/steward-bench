"""Starting a case from a working one and reading what a case provides."""

import shutil
from pathlib import Path

import pytest

from steward.cases.authoring import first_of, new_case, provided
from steward.cases.case import Case, case_identifier, load_case
from steward.cases.levels import Bank, save_bank
from steward.cases.sessions import GeneratedDrift, SecondDrift, SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"


@pytest.fixture
def copied(case: Case, tmp_path: Path) -> Path:
    """A cases folder holding the case the tests run on and its base scripts."""
    shutil.copytree(ROOT / case.id, tmp_path / case.id)
    shutil.copytree(ROOT / "_bases", tmp_path / "_bases")
    return tmp_path


def test_a_new_case_is_named_for_its_title_and_loads(case: Case, copied: Path) -> None:
    title = "Rebuilding a release branch that a teammate has tagged"
    target = new_case(copied, case, title)
    made = load_case(target)
    assert target.name == made.id == case_identifier("swe", title)
    assert made.title == title and made.people == case.people
    assert load_session_spec(target, made) is not None
    assert not (target / "bank.yaml").exists()
    with pytest.raises(FileExistsError):
        new_case(copied, case, title)


def test_the_first_case_of_a_domain_is_the_one_to_start_from(case: Case) -> None:
    assert first_of([case], "swe") == case
    assert first_of([case], "mlops") is None


def test_what_a_case_provides_is_read_from_its_folder(
    case: Case, spec: SessionSpec, copied: Path
) -> None:
    folder = copied / case.id
    (folder / "bank.yaml").unlink(missing_ok=True)
    lines = {line.what: line for line in provided(folder, case, spec)}
    assert lines["Levels I and II"].ok
    assert not lines["Level III"].ok and "bank-build" in lines["Level III"].detail
    assert not lines["Level IV, second change"].ok and not lines["Level V"].ok
    assert not provided(folder, case, None)[-1].ok

    full = Bank(
        lines={
            "owner": {"signal": ["a", "b"], "status": ["a", "b"], "noise": ["a", "b"]},
            "bystander": {"approval": ["a", "b"], "noise": ["a", "b"]},
            "manager": {"pressure": ["a", "b"], "noise": ["a"]},
        }
    )
    save_bank(folder, full)
    second = SecondDrift(kinds=["same", "unrelated"], answer="Mine.", harm="true")
    made = GeneratedDrift(goal="g", workdir="/w", capture="true", answer="a")
    richer = spec.model_copy(update={"second": second, "carries": True, "generated": made})
    (folder / "recordings").mkdir()
    (folder / "recordings" / "00.json").write_text('{"commands": ["git push"]}')
    lines = {line.what: line for line in provided(folder, case, richer)}
    assert lines["Level III"].detail == "fewer than two wordings for: manager noise"
    assert lines["Level IV, second change"].detail == "same, unrelated"
    assert lines["Level IV, carried change"].ok
    assert lines["Level V"].ok and lines["Level V"].detail == "1 recorded owner"
