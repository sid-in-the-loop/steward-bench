"""What the simulator is shown of the people of every benchmark task, and what they are asked about."""

import re
import shutil
from functools import cache
from pathlib import Path

import pytest
import yaml

from steward.cases.case import Case, Person, Role, case_directories, load_case
from steward.cases.casting import in_character, library, session_description
from steward.cases.schedule import benchmark_setting, conditions_of, has_instruction_drift
from steward.cases.sessions import (
    ScriptedItems,
    Session,
    SessionSpec,
    filled,
    load_session_spec,
)
from steward.cases.sim_probe import routine_shift, words
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


LINKING = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "for",
        "in",
        "on",
        "to",
        "with",
        "and",
        "at",
        "from",
        "your",
        "her",
        "his",
        "their",
        "its",
    ]
)


def named(text: str) -> set[str]:
    """The words, numbers and slots a text names, without articles and the like."""
    found = re.findall(r"\{\w+\}|[a-z0-9][a-z0-9'’-]*", text.lower())
    return {re.sub(r"['’]s$", "", token) for token in found} - LINKING


# A name, a number, a slot or a file-like token: something the agent finds only by looking.
IDENTIFIER = re.compile(r"\{\w+\}|\d|[a-z]+[_./-][a-z0-9]|\b[A-Z][a-z']+")


def beyond_request(spec: SessionSpec) -> tuple[set[str], set[str]]:
    """The content words and the identifiers of `owner_thing` that the request does not use."""
    request = spec.task.lower()
    extra = set(words(spec.owner_thing)) - set(words(spec.task))
    found = {m[0] for m in IDENTIFIER.finditer(spec.owner_thing.removeprefix("the "))}
    return extra, {f for f in found if f.lower() not in request}


def test_no_owner_thing_can_be_written_from_the_request_alone() -> None:
    from_request = [
        case.id for case, spec in benchmark_tasks() if named(spec.owner_thing) <= named(spec.task)
    ]
    assert from_request == []
    # Two content words the request does not use, or one identifier it does not name.
    thin = {}
    for case, spec in benchmark_tasks():
        extra, identifiers = beyond_request(spec)
        if len(extra) < 2 and not identifiers:
            thin[case.id] = spec.owner_thing
    assert thin == {}


def test_no_owner_status_line_names_the_thing() -> None:
    named_in_status = {}
    for case, spec in benchmark_tasks():
        thing = set(words(spec.owner_thing)) - set(words(spec.task))
        for line in spec.owner_status:
            shared = thing & set(words(line))
            if len(shared) >= 2:
                named_in_status[case.id] = sorted(shared)
    assert named_in_status == {}


# Wording that dates a role, hands it over or disclaims it: what settles most conflicts, which
# a side shown it on every shift could concede or claim from who they are.
SAY = re.compile(
    r"apart from|\bexcept\b|(?:has|holds) no say|not on the|"
    r"until (?:last|this|next) (?:week|month|quarter)|since (?:last|this) (?:week|month)|"
    r"(?:days?|weeks?|months?) ago|\btook (?:\w+ ){0,3}over|\btakes? (?:\w+ ){0,3}over|"
    r"until (?:she|he|they) hands?|handed (?:\w+ ){0,3}over|for (?:the last|a) month|"
    r"\bincoming\b|(?:until|since) the handover",
    re.IGNORECASE,
)


@pytest.mark.parametrize("side", ["first", "second"])
def test_no_conflict_side_is_shown_who_has_the_say(side: str, tmp_path: Path) -> None:
    found = {}
    for case, spec in benchmark_tasks():
        assert spec.conflict is not None
        role = getattr(spec.conflict, side)
        description = shown_on_a_routine_shift(case, spec, role, tmp_path)
        said = SAY.search(description)
        if said:
            found[case.id] = said[0]
    assert found == {}
    before = "is the database administrator who decides who holds a login, apart from the"
    assert SAY.search(before) and SAY.search("who is moving on and holds no say over it")


def test_the_change_said_on_a_routine_shift_is_caught_in_every_task() -> None:
    missed = {}
    for case, spec in benchmark_tasks():
        setting = benchmark_setting(case.id, spec, conditions_of(spec)[-1], 0)
        idle = ScriptedItems(spec.safe_reference)
        session = Session(case, spec, setting, None, idle, [], None)  # type: ignore[arg-type]
        shift = routine_shift(setting, spec) or 1
        for number in range(1, shift + 1):
            session.item = number
            session._open_shift(number, False, {})
        session._record("agent", "message", "Quick question about today.")
        own = [*session._ordinary_lines("owner"), spec.owner_unchanged]
        told = f"As far as I know, {spec.owner_changed}"
        if not session.tells_change(told, own, session.seen(), {}, False):
            missed[case.id] = session.distinguishing_used(told, [*own, *session.seen()], {})
    assert missed == {}


# Words any "nothing has changed" line uses, which say nothing of what it is about.
UNCHANGED_WORDS = frozenset(
    words("nothing unusual changed anything anyone nobody heard special different new far")
)


def test_every_routine_briefing_names_what_it_is_about() -> None:
    context_free = {}
    for case, spec in benchmark_tasks():
        about = set(words(spec.owner_thing)) | set(words(spec.owner_changed))
        if not set(words(spec.owner_unchanged)) & (about - UNCHANGED_WORDS):
            context_free[case.id] = spec.owner_unchanged
    assert context_free == {}


def test_an_article_before_a_slot_is_not_doubled_by_its_value() -> None:
    values = {"meeting": "the council meeting of 3 March", "fleet": "The walking fleet"}
    assert filled("the {meeting} recording", values) == "the council meeting of 3 March recording"
    assert filled("Has the {fleet} moved?", values) == "Has the walking fleet moved?"
    assert filled("{meeting} has ended.", values) == "the council meeting of 3 March has ended."
    assert filled("the {other} file", values) == "the {other} file"


def test_a_written_session_description_is_part_of_the_version(tmp_path: Path) -> None:
    directory = ROOT / "mlops-71050a8dfb"
    case = load_case(directory)
    written = load_session_spec(directory, case)
    assert written is not None and set(written.descriptions) == {"owner", "bystander"}
    copy = tmp_path / directory.name
    shutil.copytree(directory, copy)
    raw = yaml.safe_load((copy / "session.yaml").read_text())
    del raw["descriptions"]
    (copy / "session.yaml").write_text(yaml.safe_dump(raw))
    plain = load_session_spec(copy, case)
    assert plain is not None and plain.descriptions == {}
    assert plain.version != written.version
