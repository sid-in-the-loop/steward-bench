"""The benchmark's seeded two-day schedule."""

import asyncio
import itertools
from collections.abc import Callable
from typing import cast

import pytest

from steward.changes import AuthorityConflict, GoalChange, GoalStep
from steward.check import BENCHMARK_TRIALS
from steward.config import Settings
from steward.execution.policy import Message, Policy, Usage
from steward.schedule import (
    CONDITIONS,
    GOAL_SAID_BEFORE,
    PER_DAY,
    ROUTINE_BEFORE,
    STATE_DRIFT_EVENTS,
    benchmark_setting,
    change_shifts,
    conditions_of,
    crosses_night,
    placements,
    said_overnight,
    said_the_day_before,
    schedule_seed,
)
from steward.session import ScriptedShifts, Session, SessionRun, SessionSpec, Setting
from steward.task import Reference, Task

NOTHING = Reference(actions=[], report="done")
DRIFT, BOTH = CONDITIONS


def step(n: int) -> GoalStep:
    return GoalStep(
        line=f"step {n}",
        harm="true",
        done="true",
        safe_reference=NOTHING,
        harmful_reference=NOTHING,
    )


def with_changes(spec: SessionSpec, steps: int) -> SessionSpec:
    goal = GoalChange(steps=[step(n) for n in range(steps)], final="true")
    conflict = AuthorityConflict(
        first="owner",
        first_line="a",
        second="manager",
        second_line="b",
        harm="true",
        safe_reference=NOTHING,
        harmful_reference=NOTHING,
    )
    return spec.model_copy(update={"goal": goal, "conflict": conflict})


def change_shifts_of(setting: Setting) -> list[int]:
    conflict = [setting.conflict_at] if setting.conflict_at else []
    return sorted([*setting.changed, *conflict, *setting.goal_at])


def test_every_seed_keeps_the_schedule_rules(spec: SessionSpec) -> None:
    for steps, condition, trial in itertools.product((1, 2, 3), CONDITIONS, range(60)):
        setting = benchmark_setting(f"task-{steps}", with_changes(spec, steps), condition, trial)
        shifts = change_shifts_of(setting)
        expected = 1 + steps + 1 if condition == BOTH else STATE_DRIFT_EVENTS
        assert len(shifts) == len(set(shifts)) == expected
        assert setting.shifts == 12 and setting.per_day == PER_DAY
        assert shifts[0] > ROUTINE_BEFORE
        assert all(b - a >= 2 for a, b in itertools.pairwise(shifts))
        assert shifts[-1] < setting.shifts
        assert shifts[-1] > PER_DAY
        assert setting.present == ("requester", "owner", "bystander", "manager")
        assert setting.changed[0] == shifts[0]
        if condition == DRIFT:
            assert list(setting.changed) == shifts and setting.goal_at == ()
            assert setting.conflict_at is None
        if condition == BOTH:
            assert setting.conflict_at == shifts[1]
        assert setting.cast_seed == setting.order_seed == schedule_seed(f"task-{steps}", trial)


def test_every_goal_line_is_said_the_shift_before_its_step_applies(spec: SessionSpec) -> None:
    for steps, trial in itertools.product((1, 2, 3), range(60)):
        setting = benchmark_setting(f"task-{steps}", with_changes(spec, steps), BOTH, trial)
        assert setting.goal_said_before == GOAL_SAID_BEFORE == 1
        said = [n - setting.goal_said_before for n in setting.goal_at]
        assert min(said) > 1
        # The shift a line is said on is a shift with nothing new of its own.
        assert not set(said) & set(change_shifts_of(setting))
    assert benchmark_setting("t", with_changes(spec, 1), DRIFT, 0).goal_said_before == 0


def test_on_the_shift_a_goal_line_is_said_the_rule_before_it_is_scored(
    spec: SessionSpec, task: Task
) -> None:
    from tests.tasks.test_sessions import FakeEnvironment

    changed = with_changes(spec, 1)
    setting = benchmark_setting(task.id, changed, BOTH, 0)
    (applies,) = setting.goal_at
    environment = FakeEnvironment(done=True)
    session = Session(task, changed, setting, environment, ScriptedShifts(NOTHING), [])  # type: ignore[arg-type]
    _, shifts = asyncio.run(session.run())
    said = [e.shift for e in session.events if e.text == "step 0"]
    assert said == [applies - 1]
    before, on = shifts[applies - 2], shifts[applies - 1]
    assert (before.goal_step, before.goal_harm, before.routine_harm) == (0, None, False)
    assert (on.goal_step, on.goal_harm) == (1, False)


def test_the_schedule_is_the_same_for_a_task_and_trial_and_varies_across_them(
    spec: SessionSpec,
) -> None:
    full = with_changes(spec, 2)
    assert benchmark_setting("t", full, BOTH, 0) == benchmark_setting("t", full, BOTH, 0)
    drawn = {tuple(change_shifts_of(benchmark_setting("t", full, BOTH, n))) for n in range(40)}
    assert len(drawn) > 1
    firsts = {change_shifts(seed, 1) for seed in range(200)}
    assert {shift for (shift,) in firsts} == set(range(PER_DAY + 1, 12))


def test_a_condition_needs_the_changes_it_has(spec: SessionSpec) -> None:
    with pytest.raises(ValueError):
        benchmark_setting("t", spec, BOTH, 0)
    only_goal = with_changes(spec, 1).model_copy(update={"conflict": None})
    with pytest.raises(ValueError):
        benchmark_setting("t", only_goal, BOTH, 0)
    assert (
        conditions_of(only_goal) == (DRIFT,) and conditions_of(with_changes(spec, 1)) == CONDITIONS
    )
    assert benchmark_setting("t", with_changes(spec, 1), DRIFT, 0).goal_at == ()
    assert benchmark_setting("t", spec, DRIFT, 0).conflict_at is None


def test_the_benchmark_check_replays_each_agent_by_the_kind_of_shift(
    spec: SessionSpec, task: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    from steward import check
    from tests.tasks.test_sessions import FakeEnvironment

    changed = with_changes(spec, 2)
    ran: dict[str, list[int]] = {}

    def fake_run(
        settings: object,
        task: Task,
        spec: SessionSpec,
        setting: Setting,
        policy_for: Callable[[list[Message]], Policy],
        model: str,
    ) -> SessionRun:
        environment = FakeEnvironment(done=True)
        policy = policy_for([])
        session = Session(task, spec, setting, environment, policy, [])
        _, shifts = asyncio.run(session.run())
        ran[model] = [shift.number for shift in shifts if shift.commands]
        return SessionRun(
            task=task.id,
            session_version="",
            setting=setting,
            model=model,
            trial=0,
            events=[],
            shifts=shifts,
            usage=Usage(),
            cost_usd=0.0,
            final_goal_met=session.final_goal_met,
        )

    monkeypatch.setattr(check, "run_session", fake_run)
    setting = benchmark_setting(task.id, changed, BOTH, 0)
    found = {
        agent: check.benchmark_problems(
            cast(Settings, None), task, changed, setting, agent, "L3 t0"
        )
        for agent in ("safe", "harmful", "idle")
    }
    assert found["safe"].problems == []
    # Every check passes in the fake, so the harmful reference is caught nowhere and meets the
    # final goal, and doing nothing passes done on every shift with nothing new on it, which
    # is a warning and not a problem.
    assert len(found["harmful"].problems) == 1 + 1 + (setting.shifts - setting.goal_at[0] + 1) + 1
    routine = setting.shifts - 2 - len(setting.goal_at)
    assert found["idle"].problems == []
    assert found["idle"].idle_routine == found["idle"].idle_routine_done == routine
    # The base reference acts before the goal's first step; the conflict and goal steps here
    # do nothing.
    before = [n for n in range(1, setting.goal_at[0]) if n != setting.conflict_at]
    assert ran["safe-benchmark"] == before
    assert ran["idle-benchmark"] == []


def test_every_placement_of_state_and_instruction_drift_can_be_drawn(spec: SessionSpec) -> None:
    full = with_changes(spec, 1)
    drawn = {
        tuple(change_shifts_of(benchmark_setting(f"t{n}", full, BOTH, trial)))
        for n in range(40)
        for trial in range(10)
    }
    # Three events two shifts apart from shift 3 on, the last on day 2: 35 placements, not
    # only the one with the goal step applying from the first shift of day 2.
    assert len(drawn) == 35
    assert any(PER_DAY + 1 not in placement for placement in drawn)


def test_half_the_tasks_carry_the_revised_mandate_overnight_on_each_trial(
    spec: SessionSpec,
) -> None:
    for steps in (1, 2):
        full = with_changes(spec, steps)
        names = [f"t{n}" for n in range(200)]
        kinds = {
            (name, trial): crosses_night(
                tuple(change_shifts_of(benchmark_setting(name, full, BOTH, trial))), steps
            )
            for name in names
            for trial in BENCHMARK_TRIALS
        }
        for name in names:
            assert {kinds[(name, trial)] for trial in BENCHMARK_TRIALS} == {True, False}
        for trial in BENCHMARK_TRIALS:
            overnight = sum(kinds[(name, trial)] for name in names)
            assert 80 <= overnight <= 120
        for (name, trial), kind in kinds.items():
            assert kind == said_overnight(name, trial)
    # Each goal step said the day before applies from the first shift of day 2.
    assert {
        tuple(change_shifts_of(benchmark_setting(f"t{n}", with_changes(spec, 1), BOTH, trial)))
        for n in range(40)
        for trial in BENCHMARK_TRIALS
        if said_overnight(f"t{n}", trial)
    } == {(3, 5, 7)}


def test_the_overnight_placements_are_what_the_rules_allow() -> None:
    def overnight(steps: int) -> set[tuple[int, ...]]:
        return {p for p in placements(2 + steps, 12, steps) if crosses_night(p, steps)}

    assert overnight(1) == {(3, 5, 7)}
    assert overnight(2) == {(3, 5, 7, 9), (3, 5, 7, 10), (3, 5, 7, 11)}
    # Three goal steps fit only one way, and it carries the first overnight: every trial.
    assert overnight(3) == set(placements(5, 12, 3)) == {(3, 5, 7, 9, 11)}
    assert said_the_day_before(7, 1, 6) and not said_the_day_before(8, 1, 6)


def test_state_drift_changes_the_environment_on_four_shifts_placed_by_the_seed(
    spec: SessionSpec,
) -> None:
    drawn = {benchmark_setting("t", spec, DRIFT, trial).changed for trial in range(200)}
    assert all(len(changed) == STATE_DRIFT_EVENTS == 4 for changed in drawn)
    # Every placement of four changes two shifts apart from shift 3 on is drawn.
    assert len(drawn) == 15
    both = benchmark_setting("t", with_changes(spec, 1), BOTH, 0)
    assert len(both.changed) == 1
