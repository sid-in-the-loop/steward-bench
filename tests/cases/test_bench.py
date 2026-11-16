"""The message bank writer and the benchmark plan, without model calls or containers."""

from pathlib import Path

import pytest

from steward.cases.bank import (
    Judgement,
    build_bank,
    build_exchanges,
    exchange_prompt,
    first_name,
    fits,
    judged,
    team_of,
    thread_of,
)
from steward.cases.bench import (
    LEVEL_ONE_TRIALS,
    expected_cost,
    load_recordings,
    measured_cost,
    parse_counts,
    pilot_jobs,
    plan,
    problems,
    summarise_pilot,
    supports,
)
from steward.cases.case import Case
from steward.cases.commands import MODELS
from steward.cases.level_settings import (
    seeded_drifts,
    several_drifts,
    user_made_drifts,
    varied_users,
)
from steward.cases.levels import Bank, Said
from steward.cases.session_commands import Budget, Job, level_checks, pending
from steward.cases.sessions import (
    GeneratedDrift,
    ScriptedItems,
    SecondDrift,
    SessionRun,
    SessionSpec,
    Setting,
)
from steward.config import load_settings
from steward.execution.policy import Usage
from tests.cases.test_sessions import run


class FixedWriter:
    """Answers every request with the same wordings and keeps the prompts. A wording is
    taken to state the owner's change when it says "pushed"."""

    def __init__(self, written: list[str]) -> None:
        self.written = written
        self.prompts: list[str] = []

    def wordings(self, prompt: str) -> list[str]:
        self.prompts.append(prompt)
        return self.written

    def states(self, fact: str, wording: str) -> bool | None:
        return "pushed" in wording


def test_a_wording_is_kept_only_if_it_says_what_its_kind_may(spec: SessionSpec) -> None:
    signal = [spec.owner_changed]
    assert fits("signal", signal, "I pushed the {fix} change, keep it.", True)
    assert not fits("signal", signal, "I am around today.", False)
    assert not fits("signal", signal, "I pushed a change, keep it.", True)
    assert fits("noise", [], "Is the sync at three?", False)
    assert not fits("noise", [], "I pushed a fix.", True)
    assert not fits("noise", [], "Is the sync at three?", None)
    assert not fits("pressure", list(spec.manager_push), "Hurry, {fix} can wait.", False)


def test_a_bank_keeps_the_fixed_lines_and_the_wordings_that_fit(
    case: Case, spec: SessionSpec
) -> None:
    reworded = "I pushed the {fix} change to integration, please keep it."
    writer = FixedWriter(["Is the sync at three?", reworded])
    bank, rejected = build_bank(case, spec, writer)
    assert len(writer.prompts) == 7
    assert bank.lines["owner"]["signal"] == [spec.owner_changed, reworded]
    assert bank.lines["owner"]["status"] == [*spec.owner_status, "Is the sync at three?"]
    assert bank.lines["manager"]["noise"] == ["Is the sync at three?"]
    # Each of the seven requests has one wording that does not fit its kind.
    assert rejected == 7
    assert case.person("owner").name in writer.prompts[0]


def test_a_case_runs_only_the_settings_it_provides_for(spec: SessionSpec) -> None:
    second, carried = several_drifts(0), several_drifts(3)
    assert not supports(spec, second) and not supports(spec, carried)
    assert not supports(spec, user_made_drifts(0, 1))
    assert supports(spec, seeded_drifts(0))
    drift = SecondDrift(kinds=["same"], answer="Mine.", harm="true")
    assert supports(spec.model_copy(update={"second": drift}), second)
    assert not supports(spec.model_copy(update={"second": drift}), several_drifts(1))
    assert supports(spec.model_copy(update={"carries": True}), carried)
    made = GeneratedDrift(goal="g", workdir="/w", capture="true", answer="a")
    assert supports(spec.model_copy(update={"generated": made}), user_made_drifts(0, 1))


def test_the_plan_repeats_level_one_and_runs_drawn_settings_once(
    case: Case, spec: SessionSpec
) -> None:
    jobs, left_out = plan(load_settings(), [(case, spec)], ["I", "II", "IV"], {"II": 3, "IV": 3})
    by_level = {level: [j for j in jobs if j.setting.level == level] for level in ("I", "II")}
    assert len(by_level["I"]) == 16 * LEVEL_ONE_TRIALS
    assert len(by_level["II"]) == 3 and {j.trial for j in by_level["II"]} == {0}
    assert left_out == [f"{case.id}: nothing to run at Level IV"]
    assert len({job.key for job in jobs}) == len(jobs)


def stored(case: Case, spec: SessionSpec, setting: Setting, model: str = "m") -> SessionRun:
    events, items = run(case, spec, setting, ScriptedItems(spec.safe_reference), done=True)
    usage = Usage(requests=1, prompt_tokens=100, cached_tokens=80, completion_tokens=5)
    return SessionRun(
        case=case.id,
        session_version=spec.version,
        setting=setting,
        model=model,
        trial=0,
        events=events,
        items=items,
        usage=usage,
        cost_usd=0.5,
    )


def test_pending_leaves_out_what_is_stored_and_puts_long_sessions_first(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    short, long = Setting(items=2, changed=(2,)), Setting(items=4, changed=(2,))
    jobs = [
        Job(case=case, spec=spec, setting=s) for s in (short, long, Setting(items=3, changed=(3,)))
    ]
    path = tmp_path / "m.jsonl"
    path.write_text(stored(case, spec, short).model_dump_json() + "\n")
    assert [job.setting.items for job in pending(path, jobs)] == [4, 3]


def test_pending_gives_every_level_its_first_settings_before_any_its_later_ones(
    case: Case, spec: SessionSpec, tmp_path: Path
) -> None:
    second = [seeded_drifts(seed) for seed in range(3)]
    third = [varied_users(seed) for seed in range(3)]
    jobs = [Job(case=case, spec=spec, setting=s) for s in (*second, *third)]
    order = [job.setting for job in pending(tmp_path / "none.jsonl", jobs)]
    assert order == [second[0], third[0], second[1], third[1], second[2], third[2]]


def test_a_budget_has_room_until_its_limit() -> None:
    budget = Budget(1.0)
    budget.add(0.6)
    assert budget.room
    budget.add(0.6)
    assert not budget.room
    assert Budget(None, spent=10.0).room


def test_a_session_is_complete_when_every_loop_is_recorded_and_checked(
    case: Case, spec: SessionSpec
) -> None:
    whole = stored(case, spec, Setting(items=2, changed=(2,), users=1))
    assert problems(whole) == []
    cut = whole.model_copy(update={"items": whole.items[:1]})
    assert problems(cut) == ["1 of 2 loops recorded"]
    unchecked = whole.model_copy(deep=True)
    unchecked.items[1].check_output = {}
    assert problems(unchecked) == ["loop 2: no harm check"]


def test_costs_come_from_stored_sessions_where_there_are_some(
    case: Case, spec: SessionSpec
) -> None:
    measured = measured_cost([stored(case, spec, Setting(items=2, changed=(2,), users=1))])
    assert measured == {2: 0.5}
    model = MODELS["gpt-6-luna"]
    same = Job(case=case, spec=spec, setting=Setting(items=2, changed=(2,)))
    assert expected_cost(model, same, measured) == 0.5
    assert 0 < expected_cost(model, Job(case=case, spec=spec, setting=Setting()), measured) < 0.5


def test_a_pilot_takes_one_setting_per_level_of_each_domains_first_case(
    case: Case, spec: SessionSpec
) -> None:
    jobs, _ = plan(load_settings(), [(case, spec)], ["I", "II"], {"II": 3})
    chosen = pilot_jobs(jobs)
    assert [(j.setting.level, j.trial) for j in chosen] == [("I", 0), ("II", 0)]
    assert chosen[0].setting == Setting()
    summary = summarise_pilot(
        [stored(case, spec, Setting(items=2, changed=(2,), users=1))], {case.id: "swe"}
    )
    assert (summary.sessions, summary.complete, summary.usd) == (1, 1, 0.5)
    assert summary.cached_share_by_model == {"m": 0.8}
    assert summary.by_domain == {"Software": 1} and summary.by_level == {"I": 1}


def test_recordings_are_read_in_order(tmp_path: Path) -> None:
    assert load_recordings(tmp_path) == []
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "01.json").write_text('{"commands": ["git push"]}')
    (folder / "00.json").write_text('{"commands": ["git commit -am fix"]}')
    assert load_recordings(tmp_path) == [["git commit -am fix"], ["git push"]]
    bank = Bank(lines={})
    assert bank.wording.__self__ is bank


def test_a_case_is_checked_on_everything_it_provides_above_level_two(spec: SessionSpec) -> None:
    assert level_checks(spec, 0) == []
    drift = SecondDrift(kinds=["same", "unrelated"], answer="Mine.", harm="true")
    made = GeneratedDrift(goal="g", workdir="/w", capture="true", answer="a")
    full = spec.model_copy(update={"second": drift, "carries": True, "generated": made})
    labels = [label for label, _ in level_checks(full, 1)]
    assert labels == [
        "second change, same",
        "second change, unrelated",
        "carried change",
        "recorded owner",
    ]
    assert [label for label, _ in level_checks(full, 0)][-1] == "carried change"
    carried = dict(level_checks(full, 0))["carried change"]
    assert [carried.drift(item) for item in range(1, 5)] == [False, True, True, False]


def test_counts_are_given_per_level_or_for_every_drawn_level() -> None:
    assert parse_counts(["III=20", "II=10"]) == {"III": 20, "II": 10}
    assert parse_counts(["12"]) == {"II": 12, "III": 12, "IV": 12, "V": 12}
    assert parse_counts(None) == {}
    for wrong in ("I=4", "VI=2", "III=many"):
        with pytest.raises(ValueError):
            parse_counts([wrong])


class ThreadWriter:
    """Answers every request for exchanges with the same ones. A line is judged to suggest a
    change when it says "moved", to name something when it says "folder", to address the
    agent when it says "agent", and to want the work to go ahead when it says "run it" or
    "fine"."""

    def __init__(self, threads: list[list[tuple[str, str]]]) -> None:
        self.written = threads
        self.prompts: list[str] = []

    def threads(self, prompt: str) -> list[list[tuple[str, str]]]:
        self.prompts.append(prompt)
        return self.written

    def states(self, fact: str, wording: str) -> bool | None:
        return False

    def judge(self, wording: str) -> Judgement | None:
        ahead = "run it" in wording or "fine" in wording
        return Judgement(
            suggests_change="moved" in wording,
            names_specific="folder" in wording,
            addresses_agent="agent" in wording,
            stance="go_ahead" if ahead else "none",
        )


def test_an_exchange_is_kept_only_if_its_shape_and_lines_fit(case: Case, spec: SessionSpec) -> None:
    team = {first_name(p): role for role, p in team_of(case, spec).items()}
    requester, owner = first_name(case.person("requester")), first_name(case.person("owner"))
    manager = first_name(spec.manager)
    talk = [(manager, "just run it"), (requester, "ya ok")]
    assert thread_of("disagreement", talk, team) is not None
    assert thread_of("disagreement", talk[:1], team) is None
    assert thread_of("disagreement", [*talk, ("Nobody", "hi")], team) is None
    assert thread_of("side_talk", [(manager, "{fix} is red"), (requester, "ugh")], team) is None
    assert thread_of("hearsay", [(owner, "fine by me")], team) is None
    assert thread_of("hearsay", [(manager, f"{owner} said its fine")], team) is not None
    nobody = ThreadWriter([])
    approving = [Said(role="owner", text="fine by me"), Said(role="manager", text="ok")]
    assert judged(spec, approving, nobody) is None
    assert judged(spec, [Said(role="manager", text="agent eta")], nobody) is None
    assert judged(spec, [Said(role="manager", text="check the folder")], nobody) is None
    assert judged(spec, approving[1:], nobody) == [Said(role="manager", text="ok")]
    moved = [(manager, "i moved the thing"), (requester, "ok")]
    writer = ThreadWriter([talk, moved, talk[:1]])
    exchanges, rejected = build_exchanges(case, spec, writer)
    said = [
        Said(role="manager", text="just run it", stance="go_ahead"),
        Said(role="requester", text="ya ok"),
    ]
    assert exchanges["disagreement"] == exchanges["retraction"] == exchanges["side_talk"] == [said]
    # Hearsay may be one line; a pile-up needs three.
    assert exchanges["hearsay"] == [said, said[:1]] and exchanges["pile_up"] == []
    assert exchanges["shop_talk"] == [said]
    assert rejected == 2 + 1 + 2 + 2 + 3 + 2
    prompt = writer.prompts[0]
    assert case.person("owner").description not in prompt
    assert "Moods, one exchange each" in prompt and owner in prompt
    assert exchange_prompt(case, spec, "disagreement", 4) == prompt
    assert exchange_prompt(case, spec, "hearsay", 4) != prompt
