import pytest
from pydantic import ValidationError

from steward.schema import CriterionScore, Step, Trajectory
from tests.conftest import make_trajectory


def test_trajectory_id_must_carry_source() -> None:
    with pytest.raises(ValidationError):
        Trajectory(
            trajectory_id="other:1", source="fixture", domain="swe", real_user=False, steps=[]
        )


def test_step_indices_must_be_contiguous() -> None:
    steps = [Step(index=0, kind="user", content="a"), Step(index=2, kind="user", content="b")]
    with pytest.raises(ValidationError):
        Trajectory(
            trajectory_id="fixture:1", source="fixture", domain="swe", real_user=False, steps=steps
        )


def test_counts() -> None:
    trajectory = make_trajectory(n_actions=10)
    assert trajectory.agent_actions() == 10
    assert trajectory.user_messages() == 2


def test_high_scores_need_evidence() -> None:
    with pytest.raises(ValidationError):
        CriterionScore(score=2, steps=[], excerpt="")
    with pytest.raises(ValidationError):
        CriterionScore(score=3, steps=[4], excerpt=" ")
    assert CriterionScore(score=1, steps=[], excerpt="").score == 1


def test_excerpt_length_capped() -> None:
    with pytest.raises(ValidationError):
        CriterionScore(score=2, steps=[1], excerpt="x" * 201)
