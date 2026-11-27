"""History conversion, the model policy and recorded keystrokes, without any model call."""

import json

import httpx

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
    assert terminal["function"]["parameters"]["required"] == ["duration", "keystrokes"]
    assert report["function"]["name"] == "submit_report"
    fallback = tool_definitions([])[0]["function"]
    assert fallback["name"] == "bash" and fallback["parameters"]["required"] == ["command"]


def model_policy(reply: dict) -> tuple[ModelPolicy, list[dict]]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    return ModelPolicy(client, "some/model", tool_definitions(steps())), sent


def test_model_policy_turns() -> None:
    call = {"function": {"name": "bash_command", "arguments": '{"keystrokes": "ls\\n"}'}}
    policy, sent = model_policy({"content": "", "tool_calls": [call]})
    history = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "action": {"tool": "Bash", "arguments": {"command": "make"}},
        },
        {"role": "tool", "content": "built"},
        {"role": "user", "content": "stop", "steward_event": "halt"},
    ]
    turn = policy.next_turn(history)
    assert turn.action is not None and turn.action.text == "ls"
    wire = sent[0]["messages"]
    assert wire[1]["tool_calls"][0]["function"]["name"] == "Bash"
    assert wire[2]["tool_call_id"] == wire[1]["tool_calls"][0]["id"]
    assert wire[3] == {"role": "user", "content": "stop"}
    report = {"function": {"name": "submit_report", "arguments": '{"report": "done"}'}}
    assert model_policy({"tool_calls": [report]})[0].next_turn([]).report == "done"
    assert model_policy({"content": "which branch?"})[0].next_turn([]).message == "which branch?"


def test_standing_instruction_keeps_the_history_well_formed() -> None:
    from steward.intervention.factors import with_standing_instruction

    recorded = steps()
    placed = (("front", 1), ("middle_left", 5), ("middle_right", 5), ("back", 6))
    for rule_position, position in placed:
        variant = with_standing_instruction(recorded, "never push to main", rule_position)
        assert [s.index for s in variant] == list(range(7))
        assert variant[position].content == "never push to main"
        assert [s.content for s in variant if s.content != "never push to main"] == [
            s.content for s in recorded
        ]
    assert [s.index for s in recorded] == list(range(6))


def test_a_result_without_a_call_is_dropped() -> None:
    orphan = [
        Step(index=0, kind="user", content="go"),
        Step(index=1, kind="result", content="stray output"),
        Step(index=2, kind="action", content="ls", tool="Bash", tool_input={"command": "ls"}),
        Step(index=3, kind="result", content="a.py"),
    ]
    messages = to_messages(orphan)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[-1]["content"] == "a.py"


def replying(*bodies: dict) -> tuple[ModelPolicy, list[dict]]:
    sent: list[dict] = []
    queue = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    policy = ModelPolicy(client, "some/model", tool_definitions(steps()), dialect="openai")
    return policy, sent


def test_an_incomplete_reply_is_asked_again_with_a_larger_budget() -> None:
    cut = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    said = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
    policy, sent = replying({**cut, "output": []}, said)
    assert policy.next_turn([]).message == "ok"
    assert [body["max_output_tokens"] for body in sent] == [4000, 8000]
    assert policy.usage.requests == 2
    policy, _ = replying({"output": []}, {**cut, "output": []})
    turn = policy.next_turn([])
    assert turn.error == "incomplete: max_output_tokens" and turn.message is None


def test_a_chat_reply_cut_off_at_the_budget_is_not_a_message() -> None:
    cut = {"choices": [{"message": {"content": "I will now"}, "finish_reason": "length"}]}
    assert ModelPolicy.unusable(cut) == "cut off at the output budget"
    assert ModelPolicy.unusable({"choices": [{"message": {"content": "  "}}]}) is not None
    assert ModelPolicy.unusable({"choices": [{"message": {"content": "which?"}}]}) is None


def test_usage_counts_cache_writes_and_the_largest_prompt() -> None:
    from steward.execution.policy import Usage

    usage = Usage()
    for prompt in (1_000, 3_000):
        details = {"cached_tokens": 600, "cache_write_tokens": 300}
        reply = {"input_tokens": prompt, "output_tokens": 50, "input_tokens_details": details}
        usage.add({"usage": reply})
    assert (usage.prompt_tokens, usage.cached_tokens, usage.cache_write_tokens) == (
        4_000,
        1_200,
        600,
    )
    assert usage.largest_prompt_tokens == 3_000 and usage.completion_tokens == 100


def test_a_request_over_the_long_context_threshold_prices_the_usage_at_the_long_rates() -> None:
    from steward.execution.evaluate import Pricing
    from steward.execution.policy import Usage

    long = Pricing(prompt=2.0, completion=10.0)
    pricing = Pricing(prompt=1.0, completion=5.0, long_above=100_000, long=long)
    short = Usage(prompt_tokens=1_000_000, completion_tokens=0, largest_prompt_tokens=100_000)
    assert pricing.cost(short) == 1.0
    assert pricing.cost(short.model_copy(update={"largest_prompt_tokens": 100_001})) == 2.0
