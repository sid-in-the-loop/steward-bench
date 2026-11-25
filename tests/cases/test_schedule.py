"""The benchmark's seeded two-day schedule."""

import asyncio
import itertools
from collections.abc import Callable
from typing import cast

import pytest

from steward.cases.case import Case, Reference
from steward.cases.changes import AuthorityConflict, GoalChange, GoalStep
from steward.cases.schedule import (
    PER_DAY,
    ROUTINE_BEFORE,
    benchmark_setting,
    change_shifts,
    schedule_seed,
)
from steward.cases.sessions import Session, SessionRun, SessionSpec, Setting
from steward.config import Settings
from steward.execution.policy import Message, Policy, Usage

NOTHING = Reference(actions=[], report="done")


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
    for steps, level, trial in itertools.product((1, 2, 3), (1, 2, 3), range(60)):
        setting = benchmark_setting(f"case-{steps}", with_changes(spec, steps), level, trial)
        shifts = change_shifts_of(setting)
        expected = 1 + (steps if level >= 2 else 0) + (1 if level == 3 else 0)
        assert len(shifts) == len(set(shifts)) == expected
        assert setting.items == 12 and setting.per_day == PER_DAY
        assert shifts[0] > ROUTINE_BEFORE
        assert all(b - a >= 2 for a, b in itertools.pairwise(shifts))
        assert shifts[-1] < setting.items
        assert shifts[-1] > PER_DAY
        assert setting.present == ("requester", "owner", "bystander", "manager")
        assert setting.changed[0] == shifts[0]
        if level == 3:
            assert setting.conflict_at == shifts[1]
        assert setting.cast_seed == setting.order_seed == schedule_seed(f"case-{steps}", trial)


def test_the_schedule_is_the_same_for_a_task_and_trial_and_varies_across_them(
    spec: SessionSpec,
) -> None:
    full = with_changes(spec, 2)
    assert benchmark_setting("t", full, 3, 0) == benchmark_setting("t", full, 3, 0)
    drawn = {tuple(change_shifts_of(benchmark_setting("t", full, 3, n))) for n in range(40)}
    assert len(drawn) > 1
    firsts = {change_shifts(seed, 1) for seed in range(200)}
    assert {shift for (shift,) in firsts} == set(range(PER_DAY + 1, 12))


def test_a_level_needs_the_changes_it_stacks(spec: SessionSpec) -> None:
    with pytest.raises(ValueError):
        benchmark_setting("t", spec, 2, 0)
    only_goal = with_changes(spec, 1).model_copy(update={"conflict": None})
    with pytest.raises(ValueError):
        benchmark_setting("t", only_goal, 3, 0)
    assert benchmark_setting("t", only_goal, 2, 0).conflict_at is None
    assert benchmark_setting("t", spec, 1, 0).goal_at == ()


def test_the_validation_schedule_is_unchanged(spec: SessionSpec) -> None:
    from steward.cases.session_commands import changes_setting

    setting = changes_setting(with_changes(spec, 1))
    assert (setting.changed, setting.conflict_at, setting.goal_at) == ((2,), 3, (4,))
    assert (setting.users, setting.per_day, setting.goal_said_before) == (1, 3, 1)


def test_the_benchmark_check_replays_each_agent_by_the_kind_of_shift(
    spec: SessionSpec, case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    from steward.cases import session_commands
    from tests.cases.test_sessions import FakeEnvironment

    changed = with_changes(spec, 2)
    ran: dict[str, list[int]] = {}

    def fake_run(
        settings: object,
        case: Case,
        spec: SessionSpec,
        setting: Setting,
        policy_for: Callable[[list[Message]], Policy],
        model: str,
    ) -> SessionRun:
        environment = FakeEnvironment(done=True)
        policy = policy_for([])
        session = Session(case, spec, setting, environment, policy, [])
        _, items = asyncio.run(session.run())
        ran[model] = [item.item for item in items if item.commands]
        return SessionRun(
            case=case.id,
            session_version="",
            setting=setting,
            model=model,
            trial=0,
            events=[],
            items=items,
            usage=Usage(),
            cost_usd=0.0,
            final_goal_met=session.final_goal_met,
        )

    monkeypatch.setattr(session_commands, "run_session", fake_run)
    setting = benchmark_setting(case.id, changed, 3, 0)
    found = {
        agent: session_commands.benchmark_problems(
            cast(Settings, None), case, changed, setting, agent, "L3 t0"
        )
        for agent in ("safe", "harmful", "idle")
    }
    assert found["safe"].problems == []
    # Every check passes in the fake, so the harmful reference is caught nowhere and meets the
    # final goal, and doing nothing passes done on every shift with nothing new on it, which
    # is a warning and not a problem.
    assert len(found["harmful"].problems) == 1 + 1 + (setting.items - setting.goal_at[0] + 1) + 1
    routine = setting.items - 2 - len(setting.goal_at)
    assert found["idle"].problems == []
    assert found["idle"].idle_routine == found["idle"].idle_routine_done == routine
    # The base reference acts before the goal's first step; the conflict and goal steps here
    # do nothing.
    before = [n for n in range(1, setting.goal_at[0]) if n != setting.conflict_at]
    assert ran["safe-benchmark"] == before
    assert ran["idle-benchmark"] == []
