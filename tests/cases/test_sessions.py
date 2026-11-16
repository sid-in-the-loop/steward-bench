"""The session loop, its settings and its speaking schedule, on a fake environment."""

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from steward.cases.case import Case, Person, case_identifier
from steward.cases.session_commands import item_rows
from steward.cases.sessions import (
    CLOSING,
    EXPERIMENTS,
    ScriptedItems,
    Session,
    SessionRun,
    SessionSpec,
    Setting,
    addresses,
    filled,
    inherited_with_notes,
    load_session_spec,
    speakers_before,
)
from steward.execution.policy import Turn, Usage
from steward.execution.record import Action

ROOT = Path(__file__).parents[2] / "cases"


class FakeEnvironment:
    """Prepares every item with one value and fails the harm check when told to."""

    def __init__(self, harmed: bool = False, done: bool = False) -> None:
        self.harmed = harmed
        self.done = done
        self.ran: list[str] = []

    async def execute(self, action: Action) -> str:
        return f"ran {action.text}"

    async def run(self, command: str) -> str:
        self.ran.append(command)
        return ""

    async def succeeds(self, command: str) -> bool:
        return self.done

    async def checked(self, command: str, limit: int = 400) -> tuple[bool, str]:
        self.ran.append(command)
        if "set -euo pipefail" in command:
            return True, "fix=late_penalty"
        return not self.harmed, "checked"


class Asks:
    """Sends the same messages in every item, then reports."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.left: list[str] = []

    def begin_item(self, values: dict[str, str]) -> None:
        self.left = list(self.messages)

    def next_turn(self, messages: list[dict[str, object]]) -> Turn:
        return Turn(message=self.left.pop(0)) if self.left else Turn(report="Done.")


class Echo:
    model = "echo"

    def reply(self, case: Case, person: Person, stance: str, chat: list[str]) -> str | None:
        return f"As I said, {person.role} here"


def run(case: Case, spec: SessionSpec, setting: Setting, policy: object, **kwargs: object):
    environment = kwargs.pop("env", None) or FakeEnvironment(
        bool(kwargs.pop("harmed", False)), bool(kwargs.pop("done", False))
    )
    session = Session(case, spec, setting, environment, policy, [], **kwargs)  # type: ignore[arg-type]
    return asyncio.run(session.run())


def test_a_setting_rejects_a_changed_item_outside_the_session() -> None:
    with pytest.raises(ValidationError):
        Setting(items=4, changed=(5,))
    with pytest.raises(ValidationError):
        Setting(users=5)


def test_every_experiment_holds_the_baseline_or_differs_in_what_it_varies() -> None:
    ids = {s.id for settings in EXPERIMENTS.values() for s in settings}
    assert Setting().id == "i16-c9-u2-out_of_date-regular" and Setting().id in ids
    assert [s.items for s in EXPERIMENTS["length"]] == [2, 4, 16, 64]
    assert [s.users for s in EXPERIMENTS["users"]] == [1, 2, 3, 4]


def test_users_speak_repeatedly_at_their_own_rate() -> None:
    setting = Setting(items=64, changed=(33,), users=4)
    spoke = [role for item in range(1, 65) for role in speakers_before(setting, item)]
    assert (spoke.count("owner"), spoke.count("bystander"), spoke.count("manager")) == (8, 16, 8)


def test_every_user_speaks_at_least_once_in_a_short_session() -> None:
    setting = Setting(items=2, changed=(2,), users=4)
    spoke = {role for item in (1, 2) for role in speakers_before(setting, item)}
    assert spoke == {"owner", "bystander", "manager"}


def test_rare_and_frequent_speaking() -> None:
    rare = Setting(users=3, speaking="rare")
    often = Setting(users=3, speaking="frequent")
    assert [i for i in range(1, 17) if speakers_before(rare, i)] == [8]
    assert all(speakers_before(often, i) == ["owner", "bystander"] for i in range(1, 17))


def test_a_session_reads_the_measures_after_every_item(case: Case, spec: SessionSpec) -> None:
    setting = Setting(items=3, changed=(2,), users=1)
    events, items = run(case, spec, setting, ScriptedItems(spec.harmful_reference), harmed=True)
    assert [i.changed for i in items] == [False, True, False]
    assert [i.harm for i in items] == [None, True, None]
    assert all(i.ending == "reported" and i.commands == 3 and i.looked_first for i in items)
    assert {e.item for e in events} == {1, 2, 3}


def test_the_owner_answers_by_whether_the_item_is_changed(case: Case, spec: SessionSpec) -> None:
    setting = Setting(items=2, changed=(2,), users=2)
    events, items = run(case, spec, setting, Asks(["Mara, did you push to integration?"]))
    said = [e.text for e in events if e.who == "owner" and e.item in (1, 2)]
    assert spec.owner_unchanged in said
    assert filled(spec.owner_changed, {"fix": "late_penalty"}) in said
    assert [i.asked for i in items] == [["owner"], ["owner"]]


def test_a_second_question_in_an_item_goes_to_the_simulator(case: Case, spec: SessionSpec) -> None:
    setting = Setting(items=1, changed=(), users=2)
    policy = Asks(["Mara, did you push?", "Mara, are you sure?"])
    events, _ = run(case, spec, setting, policy, simulator=Echo())
    simulated = [e.text for e in events if e.simulated]
    assert simulated == ["As I said, owner here"]


def test_an_agent_left_unanswered_twice_ends_the_item_waiting(
    case: Case, spec: SessionSpec
) -> None:
    setting = Setting(items=1, changed=(), users=1)
    _, items = run(case, spec, setting, Asks(["Shall I?"] * 4))
    assert items[0].ending == "waiting"


def test_a_message_that_asks_nothing_is_the_report_on_the_item(
    case: Case, spec: SessionSpec
) -> None:
    setting = Setting(items=1, changed=(), users=2)
    events, items = run(case, spec, setting, Asks(["Rebuilt it with Mara's tiers branch."]))
    assert [e.kind for e in events if e.who == "agent"] == ["report"]
    assert items[0].asked == [] and items[0].ending == "reported"


def test_a_question_to_the_requester_once_the_item_is_done_closes_it(
    case: Case, spec: SessionSpec
) -> None:
    setting = Setting(items=1, changed=(), users=1)
    events, items = run(case, spec, setting, Asks(["Rebuilt. Anything else?"]), done=True)
    assert [e.kind for e in events if e.who == "agent"] == ["report"]
    assert events[-1].text == CLOSING and items[0].asked == []


def test_a_request_to_a_named_user_is_answered_without_a_question_mark(
    case: Case, spec: SessionSpec
) -> None:
    setting = Setting(items=1, changed=(), users=2)
    events, items = run(case, spec, setting, Asks(["Mara, please confirm nothing changed."]))
    assert items[0].asked == ["owner"] and spec.owner_unchanged in [e.text for e in events]


def test_a_user_is_addressed_by_name_not_by_mention() -> None:
    assert addresses("Mara, did you push to integration?", "Mara")
    assert addresses("Thanks. @mara can you confirm?", "Mara")
    assert addresses("Did you push anything, Mara?", "Mara")
    assert not addresses("Should I keep Mara's commits on integration?", "Mara")


def test_notes_change_what_the_agent_inherits(spec: SessionSpec) -> None:
    inherited = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert inherited_with_notes(inherited, spec, "out_of_date") == inherited
    assert inherited_with_notes(inherited, spec, "missing") == inherited[:1]
    assert inherited_with_notes(inherited, spec, "accurate")[-1]["content"] == spec.accurate_note


def test_the_item_table_has_one_row_per_item_with_the_settings(
    case: Case, spec: SessionSpec
) -> None:
    setting = Setting(items=3, changed=(1, 3), users=1)
    events, items = run(case, spec, setting, ScriptedItems(spec.safe_reference))
    stored = SessionRun(
        case="s1",
        session_version=spec.version,
        setting=setting,
        model="reference",
        trial=0,
        events=events,
        items=items,
        usage=Usage(),
        cost_usd=0.0,
    )
    rows = item_rows([stored])
    assert [r["changed_number"] for r in rows] == [1, None, 2]
    assert all(r["items"] == 3 and r["users"] == 1 and r["notes"] == "out_of_date" for r in rows)


def test_the_report_counts_harm_per_setting_and_leaves_missing_ones_empty(
    case: Case, spec: SessionSpec
) -> None:
    from steward.cases.session_report import report_data, share

    setting = Setting()
    events, items = run(case, spec, setting, ScriptedItems(spec.harmful_reference), harmed=True)
    stored = SessionRun(
        case="s1",
        session_version=spec.version,
        setting=setting,
        model="m",
        trial=0,
        events=events,
        items=items,
        usage=Usage(),
        cost_usd=0.5,
    )
    data = report_data([stored], ["m"], ["s1"])
    length = data["figures"]["length"]
    assert [s and (s["count"], s["of"]) for s in length["series"]["m"]] == [
        None,
        None,
        (1, 1),
        None,
    ]
    assert data["grid"] == [{"case": "s1", "setting": setting.id, "model": "m", "harm": 1, "of": 1}]
    assert data["coverage"]["cases"] == {"s1": 1} and data["coverage"]["cost_usd"] == 0.5
    notes = next(x for x in data["findings"]["sections"] if x["id"] == "notes")
    assert "no sessions yet" in notes["analysis"] and "{" not in notes["analysis"]
    half = share([True, False])
    assert half is not None and half.low < 0.5 < half.high


def test_a_deferred_done_is_read_again_after_the_next_item(case: Case, spec: SessionSpec) -> None:
    setting = Setting(items=3, changed=(2, 3), users=1)
    waits = spec.model_copy(update={"done_deferred": True})
    _, items = run(case, waits, setting, ScriptedItems(spec.safe_reference), done=True)
    # Item 2 is followed by item 3; item 3 is the last, so nothing follows it.
    assert [i.done_later for i in items] == [None, True, None]
    _, plain = run(case, spec, setting, ScriptedItems(spec.safe_reference), done=True)
    assert [i.done_later for i in plain] == [None, None, None]


def session_copy(folder: Path, levels: str | None, **fields: object) -> Path:
    """A copy of the s1 session with only the given Level IV and V fields."""
    folder.mkdir()
    shutil.copy(ROOT / "swe-082dc18eaa" / "item.sh", folder / "item.sh")
    raw = yaml.safe_load((ROOT / "swe-082dc18eaa" / "session.yaml").read_text())
    for name in ("levels", "carries", "second", "generated"):
        raw.pop(name, None)
    if levels is not None:
        (folder / "levels.sh").write_text(levels)
        raw["levels"] = "levels.sh"
    (folder / "session.yaml").write_text(yaml.safe_dump(raw | fields))
    return folder


def test_a_levels_script_leaves_the_session_version_as_it_was(case: Case, tmp_path: Path) -> None:
    plain = load_session_spec(session_copy(tmp_path / "plain", None), case)
    first = load_session_spec(session_copy(tmp_path / "a", "item_script\n", carries=True), case)
    other = load_session_spec(
        session_copy(tmp_path / "b", "item_script\ntrue\n", carries=True), case
    )
    assert plain is not None and first is not None and other is not None
    assert plain.levels_version == ""
    assert first.version == other.version == plain.version
    assert first.levels_version and first.levels_version != other.levels_version


def test_a_field_set_without_a_levels_script_changes_the_version(
    case: Case, tmp_path: Path
) -> None:
    plain = load_session_spec(session_copy(tmp_path / "plain", None), case)
    inline = load_session_spec(session_copy(tmp_path / "inline", None, carries=True), case)
    assert plain is not None and inline is not None
    assert inline.version != plain.version and inline.levels_version == ""


def test_the_levels_script_prepares_only_sessions_that_need_it(
    case: Case, spec: SessionSpec
) -> None:
    provided = spec.model_copy(update={"levels": "echo from-levels", "carries": True})
    plain, carried = FakeEnvironment(), FakeEnvironment()
    run(case, provided, Setting(items=2, changed=(2,), users=1), Asks([]), env=plain)
    assert not any("from-levels" in command for command in plain.ran)
    setting = Setting(level="IV", seed=0, items=3, changed=(2,), users=1, carried=1)
    run(case, provided, setting, Asks([]), env=carried)
    prepared = [command for command in carried.ran if "from-levels" in command]
    assert len(prepared) == 3
    assert all(f"item_script() (\n{spec.item}\n)\n" in command for command in prepared)
    assert "CARRIED=1" in prepared[2] and "CARRIED=0" in prepared[1]


def test_a_case_is_identified_by_its_domain_and_title() -> None:
    first = case_identifier("mlops", "Keeping the last three checkpoints of every run")
    assert first == case_identifier("mlops", "Keeping the last three checkpoints of every run")
    assert first.startswith("mlops-") and len(first) == len("mlops-") + 10
    assert first != case_identifier("dbops", "Keeping the last three checkpoints of every run")
