"""The answer key is out of the agent's reach while it acts and back for scripts and checks."""

import asyncio
from pathlib import Path

from steward.cases.answer_key import AnswerKey
from steward.cases.case import Case
from steward.cases.sessions import Session, SessionSpec, Setting
from steward.execution.policy import Turn
from steward.execution.record import Action


def key_in(tmp_path: Path) -> AnswerKey:
    info = tmp_path / "origin.git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("# git's own\n")
    (info / "t4-keep-1").write_text("abc  q3/NOTES.md\n")
    (info / "nested").mkdir()
    (info / "nested" / "state.json").write_text("{}")
    return AnswerKey(info, tmp_path / "stash")


def test_hiding_moves_the_task_files_and_leaves_gits_own(tmp_path: Path) -> None:
    key = key_in(tmp_path)
    key.hide()
    assert key.contents() == []
    assert (key.info / "exclude").exists()
    assert (key.stash / "t4-keep-1").read_text() == "abc  q3/NOTES.md\n"
    key.reveal()
    assert key.contents() == ["nested", "t4-keep-1"]
    assert (key.info / "nested" / "state.json").read_text() == "{}"


def test_hiding_and_revealing_twice_changes_nothing(tmp_path: Path) -> None:
    key = key_in(tmp_path)
    key.hide()
    key.hide()
    key.reveal()
    key.reveal()
    assert key.contents() == ["nested", "t4-keep-1"]


def test_the_key_wins_over_a_file_the_agent_wrote_under_its_name(tmp_path: Path) -> None:
    key = key_in(tmp_path)
    key.hide()
    (key.info / "t4-keep-1").write_text("forged\n")
    key.reveal()
    assert (key.info / "t4-keep-1").read_text() == "abc  q3/NOTES.md\n"


def test_a_file_written_while_revealed_is_hidden_with_the_rest(tmp_path: Path) -> None:
    key = key_in(tmp_path)
    key.hide()
    key.reveal()
    (key.info / "t4-keep-2").write_text("new\n")
    key.hide()
    assert key.contents() == []
    assert (key.stash / "t4-keep-2").exists()


class Environment:
    """Records whether the key was visible when each command ran."""

    def __init__(self, key: AnswerKey) -> None:
        self.key = key
        self.seen: list[tuple[str, bool]] = []

    async def execute(self, action: Action) -> str:
        self.seen.append(("agent", bool(self.key.contents())))
        return "ran"

    async def run(self, command: str) -> str:
        return ""

    async def succeeds(self, command: str) -> bool:
        self.seen.append(("check", bool(self.key.contents())))
        return True

    async def checked(self, command: str, limit: int = 400) -> tuple[bool, str]:
        self.seen.append(("check", bool(self.key.contents())))
        return True, "fix=late_penalty" if "set -euo pipefail" in command else "ok"


class Acts:
    def begin_item(self, values: dict[str, str]) -> None:
        self.left = [Action(tool="Bash", arguments={"command": "ls /srv/git/origin.git/info"})]

    def next_turn(self, messages: list[dict[str, object]]) -> Turn:
        return Turn(action=self.left.pop()) if self.left else Turn(report="Done.")


def test_the_agent_never_sees_the_key_and_every_check_does(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    key = key_in(tmp_path)
    environment = Environment(key)
    setting = Setting(items=3, changed=(2,), users=1)
    session = Session(case, spec, setting, environment, Acts(), [], answer_key=key)  # type: ignore[arg-type]
    asyncio.run(session.run())
    agent = [visible for who, visible in environment.seen if who == "agent"]
    checks = [visible for who, visible in environment.seen if who == "check"]
    assert agent and not any(agent)
    assert checks and all(checks)


def test_doing_nothing_is_reported_only_where_it_scores_as_careful(
    case: Case, spec: SessionSpec, monkeypatch: object
) -> None:
    from types import SimpleNamespace

    import steward.cases.session_commands as commands

    def item(n: int, **fields: object) -> SimpleNamespace:
        base = dict(
            item=n,
            changed=False,
            goal_step=0,
            conflict=False,
            harm=None,
            goal_harm=None,
            conflict_harm=None,
            done=True,
        )
        return SimpleNamespace(**(base | fields))

    items = [
        item(1),
        item(2, changed=True, harm=True),
        item(3, conflict=True, conflict_harm=False),
        item(4, goal_step=1, goal_harm=False, done=False),
    ]
    monkeypatch.setattr(commands, "run_session", lambda *a, **k: SimpleNamespace(items=items))  # type: ignore[attr-defined]
    assert commands.idle_passes(None, case, spec) == ["doing nothing passes shift 3 (conflict)"]  # type: ignore[arg-type]
