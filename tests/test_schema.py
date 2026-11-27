import pytest
from pydantic import ValidationError

from steward.schema import Step, Trajectory


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
