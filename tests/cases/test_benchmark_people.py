"""What the simulator is shown of the people of every benchmark task."""

import shutil
from functools import cache
from pathlib import Path

import pytest
import yaml

from steward.cases.case import Case, Person, Role, case_directories, load_case
from steward.cases.casting import in_character, library, session_description
from steward.cases.schedule import benchmark_setting, conditions_of, has_instruction_drift
from steward.cases.sessions import ScriptedItems, Session, SessionSpec, load_session_spec
from steward.cases.sim_probe import words
from steward.cases.simulator import BRIEFED, INSTRUCTIONS, CachedSimulator

ROOT = Path(__file__).parents[2] / "cases"
# How many content words of the owner's change the simulator may be shown on a shift without
# it, beyond the task's request, the person's own lines and the cast's names. Words such as
# "team" or "database" in who a person is are shared by chance; three or more were a fact.
LEAKED_WORDS = 3
ROLES: tuple[Role, ...] = ("requester", "owner", "bystander", "manager")


@cache
def benchmark_tasks() -> list[tuple[Case, SessionSpec]]:
    found = []
    for directory in case_directories(ROOT):
        case = load_case(directory)
        spec = load_session_spec(directory, case)
        if spec is not None and has_instruction_drift(spec):
            found.append((case, spec))
    return found


class Shown(CachedSimulator):
    """Keeps the person each reply was asked for and answers nothing."""

    def __init__(self, cache: Path) -> None:
        super().__init__(cache, "shown")
        self.people: list[Person] = []

    def reply(self, case, person, stance, chat, briefing=None):  # type: ignore[no-untyped-def]
        self.people.append(person)
        return None


def shown_on_a_routine_shift(case: Case, spec: SessionSpec, role: Role, cache: Path) -> str:
    setting = benchmark_setting(case.id, spec, conditions_of(spec)[-1], 0)
    simulator = Shown(cache)
    idle = ScriptedItems(spec.safe_reference)
    session = Session(case, spec, setting, None, idle, [], simulator)  # type: ignore[arg-type]
    session.item = 1
    session._record("agent", "message", "Quick question about today.")
    session._reply(role, False, {}, {role})
    (person,) = simulator.people
    return person.description


def leaked(case: Case, spec: SessionSpec, description: str) -> list[str]:
    """Content words of the owner's change in a description that the task's request, the
    owner's own lines, the templates, the cards' manners and the names do not account for."""
    names = [p.name for p in [*case.people, spec.manager]]
    manners = [card.describe() for card in library()]
    known = [spec.task, spec.owner_unchanged, *spec.owner_status, *names, *manners]
    known += [INSTRUCTIONS, BRIEFED]
    accounted = {w for text in known for w in words(text)}
    return sorted(set(words(spec.owner_changed)) & set(words(description)) - accounted)


def test_there_are_123_benchmark_tasks() -> None:
    assert len(benchmark_tasks()) == 123


@pytest.mark.parametrize("role", ROLES)
def test_no_one_is_shown_the_owners_change_on_a_routine_shift(role: Role, tmp_path: Path) -> None:
    found = {}
    for case, spec in benchmark_tasks():
        description = shown_on_a_routine_shift(case, spec, role, tmp_path)
        extra = leaked(case, spec, description)
        if len(extra) >= LEAKED_WORDS:
            found[case.id] = extra
    assert found == {}


def test_a_person_is_shown_who_they_are_and_how_they_write_not_what_they_know() -> None:
    case = load_case(ROOT / "terminal-4a5e7080d7")
    shown = session_description(case.person("owner"))
    assert shown.startswith("Odile Brynjulfsen, 44, is the resort's avalanche forecaster")
    assert shown.endswith("She writes short, flat messages with numbers and no greetings.")
    assert "raised the danger level" not in shown
    card = library()[0]
    assert in_character(case.person("owner"), card).description == (
        f"{shown} Manner: {card.describe()}"
    )
    assert in_character(case.person("owner"), None, "Written.").description == "Written."


def test_a_written_session_description_is_no_part_of_the_version(tmp_path: Path) -> None:
    directory = ROOT / "mlops-71050a8dfb"
    case = load_case(directory)
    written = load_session_spec(directory, case)
    assert written is not None and set(written.descriptions) == {"owner"}
    copy = tmp_path / directory.name
    shutil.copytree(directory, copy)
    raw = yaml.safe_load((copy / "session.yaml").read_text())
    del raw["descriptions"]
    (copy / "session.yaml").write_text(yaml.safe_dump(raw))
    plain = load_session_spec(copy, case)
    assert plain is not None and plain.descriptions == {}
    assert plain.version == written.version
