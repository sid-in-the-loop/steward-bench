from steward.config import RenderConfig
from steward.filter.render import (
    candidate_steps,
    estimate_tokens,
    head_tail,
    triage_summary,
    windowed_rendering,
)
from steward.schema import StepTag
from tests.conftest import make_trajectory


def test_head_tail_keeps_ends() -> None:
    text = "a" * 500 + "b" * 500
    trimmed = head_tail(text, 400, 400)
    assert trimmed.startswith("a" * 400) and trimmed.endswith("b" * 400)
    assert "200 characters omitted" in trimmed
    assert head_tail("short", 400, 400) == "short"


def test_triage_summary_fits_budget_and_lists_actions() -> None:
    trajectory = make_trajectory(n_actions=400)
    summary = triage_summary(trajectory, [StepTag(step=5, kind="irreversible_action")], 2500)
    assert estimate_tokens(summary) <= 2500
    assert "USER MESSAGES" in summary and "ACTIONS" in summary
    assert "irreversible_action: steps 5" in summary
    assert "RESULT" not in summary


def test_candidate_steps_prefers_stage_b_then_tag_priority() -> None:
    tags = [
        StepTag(step=80, kind="irreversible_action"),
        StepTag(step=10, kind="user_interruption"),
    ]
    assert candidate_steps(tags, [50], 100, 3, min_gap=15) == [10, 50, 80]
    assert candidate_steps(tags, [12], 100, 3, min_gap=15) == [12, 80]


def test_windowed_rendering_lines_carry_global_indices() -> None:
    trajectory = make_trajectory(n_actions=100)
    text = windowed_rendering(trajectory, [60, 150], RenderConfig(), token_cap=40_000)
    assert "WINDOW 1 (steps 35 to 75)" in text
    assert "[step 60]" in text
    assert "HISTORY SUMMARY" in text
    for line in text.splitlines():
        if line.startswith("[step"):
            assert line.split("]")[0][6:].isdigit()


def test_windowed_rendering_respects_cap() -> None:
    trajectory = make_trajectory(n_actions=300)
    for step in trajectory.steps:
        step.content = step.content * 200
    text = windowed_rendering(trajectory, [100, 300, 500], RenderConfig(), token_cap=5_000)
    assert estimate_tokens(text) <= 5_000
