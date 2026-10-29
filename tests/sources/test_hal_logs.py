import gzip
import json
from pathlib import Path

import pytest

from steward.config import Settings
from steward.schema import Trajectory
from steward.sources.hal_logs import HalLogs, domain_for, harness_name, task_env_for

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "hal_logs"
FEWSHOT = "hal_logs:taubench_airline_1700000000"
GENERALIST = "hal_logs:taubench_airline_hal_generalist_agent_deepseekaideepseekv3_1700000001"


@pytest.fixture
def loaded(settings: Settings) -> dict[str, Trajectory]:
    target = settings.paths.raw / "hal_logs" / "decrypted"
    target.mkdir(parents=True)
    for path in sorted(FIXTURE.glob("*.json")):
        (target / f"{path.name}.gz").write_bytes(gzip.compress(path.read_bytes()))
    (target / "broken_UPLOAD.json.gz").write_bytes(b"not gzip")
    return {t.trajectory_id: t for t in HalLogs().load(settings)}


def test_one_trajectory_per_task(loaded: dict[str, Trajectory]) -> None:
    assert sorted(loaded) == [f"{FEWSHOT}/0", f"{FEWSHOT}/1", f"{GENERALIST}/0"]


def test_tool_calling_run(loaded: dict[str, Trajectory]) -> None:
    t = loaded[f"{FEWSHOT}/0"]
    assert [s.kind for s in t.steps] == [
        "system",
        "user",
        "action",
        "result",
        "agent_text",
        "user",
        "action",
    ]
    assert [s.tool for s in t.steps if s.kind == "action"] == [
        "get_reservation_details",
        "cancel_reservation",
    ]
    assert t.steps[2].tool_input == {"reservation_id": "ABC123"}
    assert t.steps[4].content == "Shall I cancel it?"
    assert t.history_author == "claude-3-7-sonnet-20250219"
    assert t.harness == "TAU-bench FewShot"
    assert t.task_env == "tau-bench:airline:0"
    assert t.domain == "tool_use" and t.domain_decided_by_rule
    assert t.meta["reward"] == 1.0 and t.meta["successful"] is True
    assert "api_key" not in t.meta["agent_args"]
    assert "sk-should-not-appear" not in json.dumps(t.model_dump(mode="json"))
    assert loaded[f"{FEWSHOT}/1"].meta["successful"] is False


def test_code_agent_run(loaded: dict[str, Trajectory]) -> None:
    t = loaded[f"{GENERALIST}/0"]
    assert [s.kind for s in t.steps] == [
        "system",
        "user",
        "agent_text",
        "system",
        "agent_text",
        "action",
        "result",
        "agent_text",
        "action",
    ]
    action = t.steps[5]
    assert action.tool == "python_interpreter"
    assert action.content == "print(get_reservation_details(reservation_id='ABC123'))"
    assert "Calling tools" not in t.steps[4].content and "<end_code>" not in t.steps[4].content
    assert t.steps[8].tool_input == {"code": "final_answer('cancelled')"}
    assert t.history_author == "together_ai/deepseek-ai/DeepSeek-V3"
    assert t.harness == "HAL Generalist Agent"
    assert t.meta["agent_calls"] == 3


def test_benchmark_mapping() -> None:
    assert domain_for("swebench_verified_mini") == ("swe", True)
    assert domain_for("corebench_hard") == ("data", True)
    assert domain_for("new_benchmark") == ("tool_use", False)
    assert task_env_for("corebench_hard", "capsule-1") == "corebench_hard:capsule-1"
    assert (
        harness_name("HAL Generalist High Reasoning \n (gpt-5)") == "HAL Generalist High Reasoning"
    )
