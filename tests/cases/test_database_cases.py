"""The database operations cases as files: identifiers, patterns, scripts that parse, and
what each provides for the levels. Nothing here starts a container."""

import re
import subprocess
from pathlib import Path

import pytest

from steward.cases.bench import load_recordings
from steward.cases.case import Case, Repo, case_identifier, load_case
from steward.cases.sessions import SessionSpec, load_session_spec

ROOT = Path(__file__).parents[2] / "cases"
# The pilot's database cases. The scaled benchmark's tasks, which carry an authority conflict,
# are checked by their own builds.
DIRECTORIES = sorted(
    d
    for d in ROOT.glob("dbops-*")
    if not (d / ".building").exists()
    and (not (d / "session.yaml").exists() or "\nconflict:" not in (d / "session.yaml").read_text())
)
BACKFILL = case_identifier(
    "dbops", "Applying a migration to a table that a colleague's backfill is holding"
)


def loaded(directory: Path) -> tuple[Case, SessionSpec]:
    case = load_case(directory)
    spec = load_session_spec(directory, case)
    assert spec is not None
    return case, spec


def parses(script: str) -> bool:
    return (
        subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True).returncode == 0
    )


def test_there_are_five_cases_on_the_database_base() -> None:
    assert len(DIRECTORIES) == 5
    for directory in DIRECTORIES:
        case, _ = loaded(directory)
        assert case.domain == "dbops"
        assert case.id == directory.name == case_identifier("dbops", case.title)
        assert isinstance(case.environment, Repo)
        assert case.environment.base == "warehouse_db.sh"
        assert case.environment.database is not None
        assert case.environment.database.name == "warehouse"


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_patterns_compile_and_tell_the_references_apart(directory: Path) -> None:
    case, spec = loaded(directory)
    measures = case.measures
    for pattern in (measures.look, measures.change, measures.disclose):
        re.compile(pattern)
    assert case.harmful_reference is not None
    for safe, harmful in [
        (case.safe_reference, case.harmful_reference),
        (spec.safe_reference, spec.harmful_reference),
    ]:
        assert any(re.search(measures.look, action.text) for action in safe.actions)
        assert re.search(measures.change, harmful.actions[0].text)
        assert re.search(measures.disclose, safe.report, re.IGNORECASE)
        assert not re.search(measures.disclose, harmful.report, re.IGNORECASE)


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_every_script_and_reference_command_parses(directory: Path) -> None:
    case, spec = loaded(directory)
    assert case.harmful_reference is not None
    scripts = [case.setup, spec.item, spec.harm, spec.authority, spec.done]
    scripts += [case.measures.harm, case.measures.authority, case.measures.done or "true"]
    scripts += [step.run or step.meanwhile or "true" for step in case.history]
    for reference in (
        case.safe_reference,
        case.harmful_reference,
        spec.safe_reference,
        spec.harmful_reference,
    ):
        scripts += [action.text for action in reference.actions]
    if spec.second:
        scripts.append(spec.second.harm)
    if spec.generated:
        scripts.append(spec.generated.capture)
    scripts += [command for recording in load_recordings(directory) for command in recording]
    assert isinstance(case.environment, Repo) and case.environment.database is not None
    scripts.append(case.environment.database.on_start or "true")
    for script in scripts:
        assert parses(script), script


def test_the_base_script_parses() -> None:
    assert parses((ROOT / "_bases" / "warehouse_db.sh").read_text())


@pytest.mark.parametrize("directory", DIRECTORIES, ids=lambda d: d.name)
def test_each_case_provides_for_the_levels(directory: Path) -> None:
    _, spec = loaded(directory)
    assert spec.carries
    assert spec.second is not None and spec.second.role == "bystander"
    assert len(spec.second.kinds) >= 2
    assert set(spec.second.routine_harms) <= set(spec.second.kinds)
    for value in ("CARRIED", "SECOND"):
        assert value in spec.item
    recordings = load_recordings(directory)
    if directory.name == BACKFILL:
        # Her change is an open session, which a recorded owner's commands cannot hold.
        assert spec.generated is None and recordings == []
        assert spec.done_deferred
    else:
        assert spec.generated is not None and "GENERATED" in spec.item
        assert len(recordings) >= 1 and all(recordings[0])
        assert not spec.done_deferred


def test_only_the_backfill_case_leaves_done_out_of_its_single_run() -> None:
    without = [d.name for d in DIRECTORIES if load_case(d).measures.done is None]
    assert without == [BACKFILL]
