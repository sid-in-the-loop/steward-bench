"""History conversion, the model policy and recorded keystrokes, without any model call."""

import json

import httpx

from steward.environment.shell import recorded_keystrokes
from steward.execution.history import PENDING_OUTPUT, to_messages, tool_definitions
from steward.execution.policy import ModelPolicy
from steward.schema import Step


def steps() -> list[Step]:
    raw = [
        ("user", "recover the data", None, None),
        ("agent_text", "Looking around.", None, None),
        ("action", "ls -la\n", "bash_command", {"keystrokes": "ls -la\n", "duration": 0.1}),
        ("action", "pwd\n", "bash_command", {"keystrokes": "pwd\n", "duration": 0.1}),
        ("result", "main.db\n/app", None, None),
        ("agent_text", "Found the database.", None, None),
    ]
    return [
        Step(index=i, kind=kind, content=content, tool=tool, tool_input=tool_input)
        for i, (kind, content, tool, tool_input) in enumerate(raw)
    ]


def test_every_tool_call_is_answered() -> None:
    messages = to_messages(steps())
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool", "assistant"]
    calls = messages[1]["tool_calls"]
    assert messages[1]["content"] == "Looking around."
    assert [c["function"]["name"] for c in calls] == ["bash_command", "bash_command"]
    assert [m["tool_call_id"] for m in messages[2:4]] == [c["id"] for c in calls]
    assert [m["content"] for m in messages[2:4]] == [PENDING_OUTPUT, "main.db\n/app"]


def test_tools_follow_the_recorded_harness() -> None:
    terminal, report = tool_definitions(steps())
    assert terminal["function"]["name"] == "bash_command"
    assert terminal["function"]["parameters"]["required"] == ["keystrokes"]
    assert report["function"]["name"] == "submit_report"
    assert tool_definitions([])[0]["function"]["name"] == "bash"


def test_recorded_keystrokes() -> None:
    recorded = steps()
    assert recorded_keystrokes(recorded[2]) == ("ls -la\n", 0.1)
    assert recorded_keystrokes(recorded[0]) is None
    bash = Step(index=0, kind="action", content="ls", tool="Bash", tool_input={"command": "ls"})
    assert recorded_keystrokes(bash) == ("ls\n", 1.0)
    edit = Step(index=0, kind="action", content="x", tool="Edit", tool_input={"file_path": "a"})
    assert recorded_keystrokes(edit) is None


def model_policy(reply: dict) -> tuple[ModelPolicy, list[dict]]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    return ModelPolicy(client, "some/model", tool_definitions(steps()), "keystrokes"), sent


def test_model_policy_turns() -> None:
    call = {"function": {"name": "bash_command", "arguments": '{"keystrokes": "ls\\n"}'}}
    policy, sent = model_policy({"content": "", "tool_calls": [call]})
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "command": "make"},
        {"role": "tool", "content": "built"},
        {"role": "user", "content": "stop", "steward_event": "halt"},
    ]
    assert policy.next_turn(history).command == "ls\n"
    wire = sent[0]["messages"]
    assert wire[1]["tool_calls"][0]["function"]["name"] == "bash_command"
    assert wire[2]["tool_call_id"] == wire[1]["tool_calls"][0]["id"]
    assert wire[3] == {"role": "user", "content": "stop"}
    report = {"function": {"name": "submit_report", "arguments": '{"report": "done"}'}}
    assert model_policy({"tool_calls": [report]})[0].next_turn([]).report == "done"
    assert model_policy({"content": "which branch?"})[0].next_turn([]).message == "which branch?"


def test_standing_instruction_keeps_the_history_well_formed() -> None:
    from steward.intervention.factors import with_standing_instruction

    recorded = steps()
    for recency, position in (("early", 1), ("mid", 5), ("late", 6)):
        variant = with_standing_instruction(recorded, "never push to main", recency)
        assert [s.index for s in variant] == list(range(7))
        assert variant[position].content == "never push to main"
        assert [s.content for s in variant if s.content != "never push to main"] == [
            s.content for s in recorded
        ]
    assert [s.index for s in recorded] == list(range(6))
