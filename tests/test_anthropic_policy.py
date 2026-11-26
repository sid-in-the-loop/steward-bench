from types import SimpleNamespace
from typing import Any

from steward.execution.anthropic_policy import (
    MAX_TOKENS,
    NO_REPLY,
    REPORT_RECEIVED,
    RETRY_MAX_TOKENS,
    AnthropicPolicy,
    claude_tools,
)
from steward.execution.evaluate import Pricing
from steward.execution.history import REPORT_TOOL
from steward.execution.policy import Message

TOOLS: list[Message] = [
    {"type": "function", "function": {"name": "Bash", "description": "Run a command."}},
    {
        "type": "function",
        "function": {
            "name": REPORT_TOOL,
            "parameters": {"type": "object", "properties": {"report": {"type": "string"}}},
        },
    },
]


def thinking(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking=text)


def tool_use(identifier: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=identifier, name=name, input=arguments)


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


class FakeClient:
    """Answers each request with the next prepared reply and keeps what was asked."""

    def __init__(self, replies: list[list[SimpleNamespace]]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        self.messages = self
        self.stop_reason = "end_turn"

    def create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        usage = SimpleNamespace(
            input_tokens=10, cache_read_input_tokens=100, cache_creation_input_tokens=20,
            output_tokens=5,
        )  # fmt: skip
        return SimpleNamespace(
            content=self.replies.pop(0), usage=usage, stop_reason=self.stop_reason
        )


def test_tools_take_the_messages_shape() -> None:
    bash, report = claude_tools(TOOLS)
    assert bash == {
        "name": "Bash",
        "description": "Run a command.",
        "input_schema": {"type": "object", "properties": {}},
    }
    assert report["input_schema"]["properties"] == {"report": {"type": "string"}}


def test_a_session_is_sent_back_as_the_blocks_it_came_with() -> None:
    action = [thinking("look first"), tool_use("toolu_1", "Bash", {"command": "git status"})]
    report = [tool_use("toolu_2", REPORT_TOOL, {"report": "Done."})]
    question = [text("Mara, did you push?")]
    client = FakeClient([action, report, question])
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    recorded_call = {"id": "call:0", "type": "function", "function": {"name": "Bash"}}
    messages: list[Message] = [
        {"role": "system", "content": "You maintain the repository."},
        {"role": "user", "content": "Rebuild integration."},
        {"role": "assistant", "content": "", "tool_calls": [recorded_call]},
        {"role": "tool", "tool_call_id": "call:0", "content": "rebuilt"},
        {"role": "user", "content": "Rebuild it again."},
    ]

    first = policy.next_turn(messages)
    assert first.action is not None and first.action.arguments == {"command": "git status"}
    assert policy.thought == "look first"
    messages.append({"role": "assistant", "content": "", "action": first.action.model_dump()})
    messages.append({"role": "tool", "content": "clean"})

    second = policy.next_turn(messages)
    assert second.report == "Done."
    messages.append({"role": "assistant", "content": "Done."})
    messages.append({"role": "user", "content": "Next item."})

    third = policy.next_turn(messages)
    assert third.message == "Mara, did you push?"

    request = client.requests[-1]
    assert request["system"] == "You maintain the repository."
    turns = request["messages"]
    assert [turn["role"] for turn in turns] == ["user", "assistant"] * 3 + ["user"]
    assert turns[1]["content"][0]["id"] == "call_0"
    assert turns[2]["content"][0]["tool_use_id"] == "call_0"
    assert turns[3]["content"] == action
    assert turns[4]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "clean",
    }
    assert turns[5]["content"] == report
    assert turns[6]["content"][0]["content"] == REPORT_RECEIVED
    assert turns[6]["content"][1] == {"type": "text", "text": "Next item."}
    # Each request only adds to the one before it, so the cache can serve the earlier part.
    earlier = client.requests[1]["messages"]
    assert turns[: len(earlier) - 1] == earlier[:-1]


def test_cache_reads_and_writes_are_counted_and_priced() -> None:
    client = FakeClient([[text("Hello.")]])
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    policy.next_turn([{"role": "user", "content": "Hi"}])
    usage = policy.usage
    assert (usage.prompt_tokens, usage.cached_tokens, usage.cache_write_tokens) == (130, 100, 20)
    price = Pricing(prompt=1.0, cached=0.1, cache_write=1.25, completion=5.0)
    assert price.cost(usage) == (10 * 1.0 + 100 * 0.1 + 20 * 1.25 + 5 * 5.0) / 1_000_000


def test_an_empty_reply_is_asked_again_with_a_larger_budget() -> None:
    client = FakeClient([[thinking("nothing to say")], [text("ok")]])
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    messages: list[Message] = [{"role": "user", "content": "Hi"}]
    assert policy.next_turn(messages).message == "ok"
    assert [r["max_tokens"] for r in client.requests] == [MAX_TOKENS, RETRY_MAX_TOKENS]
    assert policy.usage.requests == 2


def test_a_reply_still_empty_is_a_model_error_and_not_sent_back() -> None:
    client = FakeClient([[thinking("nothing")], [], [text("ok")]])
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    messages: list[Message] = [{"role": "user", "content": "Hi"}]
    turn = policy.next_turn(messages)
    assert turn.error == "no text and no tool call" and turn.message is None
    assert policy.replies == {}
    messages += [{"role": "user", "content": "Hello?"}]
    assert policy.next_turn(messages).message == "ok"


def test_a_refusal_stop_is_asked_again() -> None:
    client = FakeClient([[text("I can't")], [text("I can't")]])
    client.stop_reason = "refusal"
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    turn = policy.next_turn([{"role": "user", "content": "Hi"}])
    assert turn.error == "stopped: refusal" and len(client.requests) == 2


def test_a_question_nobody_answered_is_closed_with_a_user_turn() -> None:
    client = FakeClient([[text("Mara, did you push?")], [text("I will go ahead.")]])
    policy = AnthropicPolicy(client, "claude-haiku-5-5", TOOLS)
    messages: list[Message] = [{"role": "user", "content": "Rebuild integration."}]
    policy.next_turn(messages)
    messages.append({"role": "assistant", "content": "Mara, did you push?"})
    policy.next_turn(messages)
    last = client.requests[-1]["messages"][-1]
    assert last == {"role": "user", "content": [{"type": "text", "text": NO_REPLY}]}
