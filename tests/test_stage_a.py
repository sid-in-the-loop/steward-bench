import math

from steward.filter import stage_a
from steward.schema import Step, StepTag
from steward.store import write_source
from tests.conftest import make_trajectory


def test_passing_trajectory(settings) -> None:
    result = stage_a.evaluate(make_trajectory(n_actions=50), settings)
    assert result.passed and result.failed_check is None
    assert result.state_changing_actions == 50


def test_each_check_fails(settings) -> None:
    assert stage_a.evaluate(make_trajectory(n_actions=39), settings).failed_check == "A1"
    read_only = make_trajectory(n_actions=45)
    for step in read_only.steps:
        if step.kind == "action":
            step.content = "cat file.py"
            step.tool_input = {"command": "cat file.py"}
    assert stage_a.evaluate(read_only, settings).failed_check == "A2"
    no_world = make_trajectory().model_copy(update={"base_commit": None})
    assert stage_a.evaluate(no_world, settings).failed_check == "A3"
    low_share = make_trajectory().model_copy(update={"meta": {"agent_percentage": 10.0}})
    assert stage_a.evaluate(low_share, settings).failed_check == "A4"
    fragment = make_trajectory().model_copy(update={"meta": {"is_continuation": True}})
    assert stage_a.evaluate(fragment, settings).failed_check == "A5"


def test_agent_only_trajectory_skips_a4(settings) -> None:
    trajectory = make_trajectory(real_user=False).model_copy(
        update={"meta": {"agent_percentage": 1}}
    )
    assert stage_a.evaluate(trajectory, settings).passed


def test_state_change_detection() -> None:
    edit = Step(index=0, kind="action", content="{}", tool="Edit")
    view = Step(
        index=0,
        kind="action",
        content="view",
        tool="str_replace_editor",
        tool_input={"command": "view"},
    )
    create = Step(
        index=0,
        kind="action",
        content="c",
        tool="str_replace_editor",
        tool_input={"command": "create"},
    )
    redirect = Step(index=0, kind="action", content="echo x > out.txt", tool="Bash")
    assert stage_a.is_state_changing(edit)
    assert not stage_a.is_state_changing(view)
    assert stage_a.is_state_changing(create)
    assert stage_a.is_state_changing(redirect)


def test_prescore_formula() -> None:
    tags = [
        StepTag(step=1, kind="user_interruption"),
        StepTag(step=2, kind="evaluation_rules"),
        StepTag(step=3, kind="evaluation_rules"),
    ]
    assert stage_a.prescore(tags, 80) == round(2 + 2 + 1 + math.log2(2), 4)
    assert stage_a.prescore([], 40) == 0.0


def test_run_writes_parquet_and_reads_back(settings) -> None:
    write_source(
        settings.paths.normalized,
        "fixture",
        [make_trajectory("a", interrupt_at=3), make_trajectory("b", n_actions=3)],
    )
    stage_a.run(settings)
    results = {r.trajectory_id: r for r in stage_a.read_results(settings)}
    assert results["fixture:a"].passed
    assert results["fixture:b"].failed_check == "A1"
    assert any(t.kind == "user_interruption" for t in results["fixture:a"].tags)


def test_mcp_write_operations() -> None:
    def action(tool: str) -> Step:
        return Step(index=0, kind="action", content="{}", tool=tool)

    for tool in ("emails-send_email", "canvas-canvas_create_course", "filesystem-write_file"):
        assert stage_a.is_state_changing(action(tool)), tool
    for tool in ("filesystem-read_file", "google_sheet-get_sheet_data", "k8s-kubectl_get"):
        assert not stage_a.is_state_changing(action(tool)), tool
