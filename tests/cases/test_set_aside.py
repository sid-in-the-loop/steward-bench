"""Setting aside stored sessions whose questions the runner sent to the wrong person."""

from steward.cases.case import Case
from steward.cases.run import Event
from steward.cases.session_commands import sessions_path
from steward.cases.sessions import SessionRun, SessionSpec, Setting
from steward.cases.set_aside import misrouted, set_aside_misrouted
from steward.config import Settings
from tests.cases.test_bench import stored
from tests.cases.test_catalogue import copy_case, store


def asking(run: SessionRun, text: str) -> SessionRun:
    question = Event(turn=99, who="agent", kind="message", text=text, item=1)
    return run.model_copy(update={"events": [*run.events, question]})


def owner_name(case: Case) -> str:
    return next(p.name for p in case.people if p.role == "owner")


def test_only_a_question_that_needs_the_full_name_is_misrouted(
    case: Case, spec: SessionSpec
) -> None:
    run = stored(case, spec, Setting(items=1, changed=()))
    full, first = owner_name(case), owner_name(case).split()[0]
    assert misrouted(asking(run, f"{full}, did anything change?"), case, spec)
    assert misrouted(asking(run, f"**{first}**, did anything change?"), case, spec)
    assert not misrouted(asking(run, f"{first}, did anything change?"), case, spec)
    assert not misrouted(run, case, spec)


def test_misrouted_sessions_move_beside_the_session_file(
    settings: Settings, case: Case, spec: SessionSpec
) -> None:
    copy_case(settings, case)
    plain = stored(case, spec, Setting())
    wrong = asking(plain, f"{owner_name(case)}: is the branch yours?")
    store(settings, "gpt-6-luna", [plain, wrong, plain])
    assert set_aside_misrouted(settings) == {"gpt-6-luna": 1}
    path = sessions_path(settings, "gpt-6-luna")
    assert path.read_text().splitlines() == [plain.model_dump_json()] * 2
    aside = (path.parent / "set_aside" / "gpt-6-luna.jsonl").read_text().splitlines()
    assert aside == [wrong.model_dump_json()]
    assert set_aside_misrouted(settings) == {}
