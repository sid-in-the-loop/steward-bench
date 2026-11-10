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


def test_excerpt_trimmed_to_cap() -> None:
    assert CriterionScore(score=2, steps=[1], excerpt="x" * 250).excerpt == "x" * 200


def test_stage_b_output_is_normalized() -> None:
    from steward.schema import StageBOutput, normalize_kind

    output = StageBOutput.model_validate(
        {
            "decision": "YES",
            "reason": "r",
            "domain": "swe",
            "best_steps": [1, 2, 3, 4, 5, 6],
            "moment_kinds": [
                "report to the user",
                "user interruption",
                "Switch-Point",
                "irreversible_action",
                "weird",
            ],
            "offline_rebuild": "likely",
        }
    )
    assert output.best_steps == [1, 2, 3, 4, 5]
    assert output.moment_kinds == [
        "user_report",
        "user_interruption",
        "switch_point",
        "irreversible_action",
        "other",
    ]
    assert normalize_kind("report_to_user") == "user_report"


def test_phase1_field_names_still_load() -> None:
    from steward.schema import CRITERIA, StageC2Output

    output = StageC2Output.model_validate(
        {
            "trajectory_id": "s:1",
            "scores": {c: {"score": 0, "steps": [], "excerpt": ""} for c in CRITERIA},
            "honest_reporting_moment": False,
            "authorization_boundary": False,
            "candidates": [
                {
                    "step": 3,
                    "phase": "editing",
                    "moment_kind": "irreversible_action",
                    "history_tokens": 10,
                    "buttons": ["--no-verify"],
                    "checkability": "command_log",
                    "right_behavior_agreement": "high",
                    "completable_without_shortcut": True,
                    "unsafe_precedent": False,
                    "conflicting_instruction": False,
                    "rebuild_dependencies": "none",
                    "evidence_steps": [3],
                }
            ],
        }
    )
    dumped = output.model_dump()["jump_in_candidates"][0]
    assert dumped["available_shortcuts"] == ["--no-verify"]
    assert dumped["detection_method"] == "command_log"
