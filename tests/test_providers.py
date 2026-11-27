"""The Gemini and Muse endpoints, against replies shaped as their API references give them.
No request leaves the process."""

import json
from typing import Any

import httpx
import pytest

from steward import smoke
from steward.execution.anthropic_policy import AnthropicPolicy
from steward.execution.gemini_policy import (
    MAX_OUTPUT_TOKENS,
    PLACEHOLDER_SIGNATURE,
    GeminiPolicy,
)
from steward.execution.history import REPORT_TOOL
from steward.execution.policy import Message
from steward.models import (
    LONG_REQUEST_SECONDS,
    MUSE_OUTPUT_TOKENS,
    Model,
    ReasoningPolicy,
    endpoint,
    model_policy,
)
from steward.rate_log import usage_of
from steward.session import SessionRun
from steward.task import Case

GEMINI_MODELS = ["gemini-4-carbon", "gemini-4-argon", "gemini-4-neon", "gemini-4-hydrogen"]
MUSE_MODELS = ["muse-wonder-1.0", "muse-spark-1.3", "muse-ember-1.0"]

TOOLS: list[Message] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "The bash tool, as used earlier in this session.",
            "parameters": {"type": "object", "properties": {"command": {}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": REPORT_TOOL,
            "description": "Finish the task and report the outcome to the user.",
            "parameters": {"type": "object", "properties": {"report": {"type": "string"}}},
        },
    },
]


class Recorder:
    """An HTTP client that answers each request with the next prepared body and keeps what
    was sent: the path, the headers and the JSON body."""

    def __init__(self, model: Model, replies: list[dict[str, Any]]) -> None:
        self.replies = replies
        self.sent: list[tuple[str, httpx.Headers, dict[str, Any]]] = []
        self.client = httpx.Client(
            base_url=model.base_url,
            headers=model.auth_headers("test-key"),
            transport=httpx.MockTransport(self.handle),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.url.path, request.headers, json.loads(request.content)))
        return httpx.Response(200, json=self.replies.pop(0))


def case() -> Case:
    return Case.model_construct(id="terminal-0000000000")


def gemini_reply(parts: list[dict[str, Any]], finish: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": 5000,
            "cachedContentTokenCount": 4000,
            "candidatesTokenCount": 40,
            "thoughtsTokenCount": 300,
            "totalTokenCount": 5340,
        },
        "modelVersion": "gemini-4-hydrogen",
    }


def gemini_call(name: str, args: dict[str, Any], call_id: str, signature: str) -> dict[str, Any]:
    return {
        "functionCall": {"id": call_id, "name": name, "args": args},
        "thoughtSignature": signature,
    }


def muse_reply(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "muse-spark-1.3",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 5000,
            "completion_tokens": 340,
            "total_tokens": 5340,
            "prompt_tokens_details": {"cached_tokens": 4000},
            "completion_tokens_details": {"reasoning_tokens": 300},
        },
    }


def muse_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


# An inherited turn with two calls, as the takeover's history gives them.
INHERITED: list[Message] = [
    {"role": "system", "content": "You maintain the repository."},
    {"role": "user", "content": "Rebuild integration."},
    {
        "role": "assistant",
        "content": "Looking around.",
        "tool_calls": [
            {"id": f"call_{n}", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
            for n in (1, 2)
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "one"},
    {"role": "tool", "tool_call_id": "call_2", "content": "two"},
    {"role": "user", "content": "Rebuild it again."},
]


@pytest.mark.parametrize("name", ["claude-opus-5-5", *GEMINI_MODELS, *MUSE_MODELS])
def test_endpoint_accepts_each_new_model_with_its_default_reasoning(name: str) -> None:
    model = endpoint(name)
    assert model.name == name and model.reasoning is not None


def test_each_provider_has_its_endpoint_and_key() -> None:
    gemini, muse = endpoint("gemini-4-neon"), endpoint("muse-ember-1.0")
    assert (gemini.dialect, gemini.api_key_env) == ("gemini", "GEMINI_API_KEY")
    assert gemini.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert gemini.auth_headers("k") == {"x-goog-api-key": "k"}
    assert (muse.dialect, muse.api_key_env) == ("openrouter", "MUSE_API_KEY")
    assert muse.base_url == "https://api.meta.ai/v1"
    assert muse.auth_headers("k") == {"Authorization": "Bearer k"}
    assert [endpoint(n).reasoning for n in GEMINI_MODELS] == ["medium", "medium", "high", "high"]
    assert [endpoint(n).reasoning for n in MUSE_MODELS] == ["max", "high", "max"]
    assert endpoint("claude-opus-5-5").reasoning == "medium"


def test_a_stored_session_without_a_reasoning_setting_still_parses() -> None:
    assert "reasoning" in SessionRun.model_fields
    assert SessionRun.model_fields["reasoning"].default is None


def test_the_claude_client_is_not_refused_a_large_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = endpoint("claude-opus-5-5")
    policy = model_policy(httpx.Client(), model, case())(TOOLS)
    assert isinstance(policy, AnthropicPolicy)
    assert policy.client.timeout == LONG_REQUEST_SECONDS  # type: ignore[attr-defined]


def test_gemini_request_has_the_generate_content_shape() -> None:
    model = endpoint("gemini-4-hydrogen")
    recorder = Recorder(model, [gemini_reply([{"text": "Which branch?"}])])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    assert isinstance(policy, GeminiPolicy)
    policy.next_turn(INHERITED)
    path, headers, body = recorder.sent[0]
    assert path == "/v1beta/models/gemini-4-hydrogen:generateContent"
    assert headers["x-goog-api-key"] == "test-key" and "authorization" not in headers
    assert body["systemInstruction"] == {"parts": [{"text": "You maintain the repository."}]}
    declarations = body["tools"][0]["functionDeclarations"]
    assert declarations[0] == {
        "name": "bash",
        "description": "The bash tool, as used earlier in this session.",
        "parametersJsonSchema": TOOLS[0]["function"]["parameters"],
    }
    # Thought summaries are asked for; the thinking level and sampling are left alone.
    assert body["generationConfig"] == {
        "maxOutputTokens": MAX_OUTPUT_TOKENS,
        "thinkingConfig": {"includeThoughts": True},
    }
    assert "temperature" not in json.dumps(body)
    contents = body["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    calls = [p["functionCall"] for p in contents[1]["parts"] if "functionCall" in p]
    assert [c["id"] for c in calls] == ["call_1", "call_2"]
    responses = [p["functionResponse"] for p in contents[2]["parts"] if "functionResponse" in p]
    assert responses == [
        {"name": "bash", "response": {"output": "one"}, "id": "call_1"},
        {"name": "bash", "response": {"output": "two"}, "id": "call_2"},
    ]
    assert contents[2]["parts"][-1] == {"text": "Rebuild it again."}
    # Before the current turn signatures are not checked, so none is made up.
    assert "thoughtSignature" not in json.dumps(contents)


def test_gemini_tool_call_reply_is_an_action_and_its_usage_is_priced() -> None:
    model = endpoint("gemini-4-carbon")
    parts = [
        {"text": "Check the tree first.", "thought": True},
        gemini_call("bash", {"command": "git status"}, "fc_1", "sig-A"),
    ]
    recorder = Recorder(model, [gemini_reply(parts)])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    turn = policy.next_turn(INHERITED)
    assert turn.action is not None
    assert (turn.action.tool, turn.action.arguments) == ("bash", {"command": "git status"})
    assert getattr(policy, "thought", None) == "Check the tree first."
    usage = policy.usage  # type: ignore[attr-defined]
    assert (usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens) == (5000, 4000, 340)
    # $4 a million fresh, $0.20 cached and $20 output, thinking included.
    assert model.pricing.cost(usage) == pytest.approx(
        (1000 * 4.00 + 4000 * 0.20 + 340 * 20.00) / 1_000_000
    )
    assert usage_of(gemini_reply(parts)) == (5000, 4000, 340)


def test_gemini_replies_go_back_with_their_signatures_across_turns() -> None:
    model = endpoint("gemini-4-neon")
    first = [gemini_call("bash", {"command": "make"}, "fc_1", "sig-A")]
    second = [gemini_call(REPORT_TOOL, {"report": "Rebuilt."}, "fc_2", "sig-B")]
    third = [{"text": "Which branch next?", "thoughtSignature": "sig-C"}]
    recorder = Recorder(model, [gemini_reply(first), gemini_reply(second), gemini_reply(third)])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    messages = list(INHERITED)

    action = policy.next_turn(messages).action
    assert action is not None
    messages.append({"role": "assistant", "content": "", "action": action.model_dump()})
    messages.append({"role": "tool", "content": "built"})
    assert policy.next_turn(messages).report == "Rebuilt."
    messages.append({"role": "assistant", "content": "Rebuilt."})
    messages.append({"role": "user", "content": "Next item."})
    assert policy.next_turn(messages).message == "Which branch next?"

    second_request = recorder.sent[1][2]["contents"]
    assert second_request[-2] == {"role": "model", "parts": first}
    assert second_request[-1]["parts"] == [
        {"functionResponse": {"name": "bash", "response": {"output": "built"}, "id": "fc_1"}}
    ]
    last = recorder.sent[2][2]["contents"]
    assert last[-2] == {"role": "model", "parts": second}
    report_answer = last[-1]["parts"][0]["functionResponse"]
    assert report_answer["id"] == "fc_2" and report_answer["name"] == REPORT_TOOL
    assert last[-1]["parts"][1] == {"text": "Next item."}
    # Each request only adds to the one before it, so the implicit cache can serve it.
    assert last[: len(second_request)] == second_request


def test_a_call_in_the_current_turn_that_the_model_did_not_write_gets_the_placeholder() -> None:
    model = endpoint("gemini-4-argon")
    recorder = Recorder(model, [gemini_reply([{"text": "Done."}])])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    policy.next_turn(INHERITED[:5])
    contents = recorder.sent[0][2]["contents"]
    calls = [p for p in contents[1]["parts"] if "functionCall" in p]
    assert calls[0]["thoughtSignature"] == PLACEHOLDER_SIGNATURE
    assert "thoughtSignature" not in calls[1]


def test_only_the_first_of_parallel_gemini_calls_is_kept() -> None:
    model = endpoint("gemini-4-hydrogen")
    parallel = [
        gemini_call("bash", {"command": "ls"}, "fc_1", "sig-A"),
        {"functionCall": {"id": "fc_2", "name": "bash", "args": {"command": "pwd"}}},
    ]
    recorder = Recorder(model, [gemini_reply(parallel), gemini_reply([{"text": "ok"}])])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    messages = list(INHERITED)
    action = policy.next_turn(messages).action
    assert action is not None and action.arguments == {"command": "ls"}
    messages.append({"role": "assistant", "content": "", "action": action.model_dump()})
    messages.append({"role": "tool", "content": "files"})
    policy.next_turn(messages)
    assert recorder.sent[1][2]["contents"][-2]["parts"] == parallel[:1]


def test_a_gemini_reply_cut_off_is_asked_again_then_ends_the_shift() -> None:
    model = endpoint("gemini-4-carbon")
    cut = gemini_reply([{"text": "Let me"}], finish="MAX_TOKENS")
    recorder = Recorder(model, [cut, cut])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    assert policy.next_turn(INHERITED).error == "stopped: MAX_TOKENS"
    assert len(recorder.sent) == 2


def test_muse_request_is_chat_completions_at_the_default_reasoning() -> None:
    model = endpoint("muse-spark-1.3")
    recorder = Recorder(model, [muse_reply({"role": "assistant", "content": "Which branch?"})])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    assert isinstance(policy, ReasoningPolicy)
    assert policy.next_turn(INHERITED).message == "Which branch?"
    path, headers, body = recorder.sent[0]
    assert path == "/v1/chat/completions"
    assert headers["authorization"] == "Bearer test-key"
    assert body["model"] == "muse-spark-1.3"
    assert body["tools"] == TOOLS
    assert body["max_tokens"] == MUSE_OUTPUT_TOKENS
    assert body["parallel_tool_calls"] is False
    assert body["prompt_cache_key"] and body["prompt_cache_retention"] == "24h"
    assert "reasoning_effort" not in body and "reasoning" not in body
    assert "temperature" not in body
    assert [m["role"] for m in body["messages"]] == [
        "system", "user", "assistant", "tool", "tool", "user"
    ]  # fmt: skip


def test_an_openai_request_sets_no_effort_and_keeps_the_one_the_reply_reports() -> None:
    model = endpoint("gpt-6.2-sol")
    reply = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Which branch?"}]}
        ],
        "reasoning": {"effort": "medium", "summary": "auto"},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    recorder = Recorder(model, [reply])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    assert isinstance(policy, ReasoningPolicy)
    assert policy.next_turn(INHERITED).message == "Which branch?"
    body = recorder.sent[0][2]
    assert body["reasoning"] == {"summary": "auto"}
    assert "temperature" not in body
    assert policy.effort == "medium"


def test_muse_tool_call_reply_is_an_action_and_its_usage_is_priced() -> None:
    model = endpoint("muse-wonder-1.0")
    recorder = Recorder(model, [muse_reply(muse_call("bash", {"command": "ls"}), "tool_calls")])
    policy = model_policy(recorder.client, model, case())(TOOLS)
    turn = policy.next_turn(INHERITED)
    assert turn.action is not None and turn.action.arguments == {"command": "ls"}
    usage = policy.usage  # type: ignore[attr-defined]
    # Reasoning tokens are part of the completion tokens, not added to them.
    assert (usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens) == (5000, 4000, 340)
    assert model.pricing.cost(usage) == pytest.approx(
        (1000 * 4.00 + 4000 * 0.20 + 340 * 20.00) / 1_000_000
    )


def test_muse_tool_calls_go_back_with_their_results_across_turns() -> None:
    model = endpoint("muse-ember-1.0")
    replies = [
        muse_reply(muse_call("bash", {"command": "make"}), "tool_calls"),
        muse_reply(muse_call(REPORT_TOOL, {"report": "Rebuilt."}), "tool_calls"),
    ]
    recorder = Recorder(model, replies)
    policy = model_policy(recorder.client, model, case())(TOOLS)
    messages = list(INHERITED)
    action = policy.next_turn(messages).action
    assert action is not None
    messages.append({"role": "assistant", "content": "", "action": action.model_dump()})
    messages.append({"role": "tool", "content": "built"})
    assert policy.next_turn(messages).report == "Rebuilt."
    wire = recorder.sent[1][2]["messages"]
    call, result = wire[-2], wire[-1]
    assert call["tool_calls"][0]["function"] == {
        "name": "bash",
        "arguments": json.dumps({"command": "make"}),
    }
    assert result == {
        "role": "tool",
        "tool_call_id": call["tool_calls"][0]["id"],
        "content": "built",
    }


def test_the_smoke_bound_covers_every_request_at_its_ceiling() -> None:
    model = endpoint("gemini-4-carbon")
    output = 2 * 3 * smoke.OUTPUT_TOKENS
    expected = (smoke.REQUESTS * smoke.INPUT_TOKENS * 4.00 + output * 20.00) / 1_000_000
    assert smoke.bound_usd(model) == pytest.approx(expected)


def test_the_smoke_makes_no_call_without_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main([]) == 0
    printed = capsys.readouterr().out
    assert "Add --confirm to run." in printed
    assert all(name in printed for name in smoke.NEW_MODELS)


@pytest.mark.parametrize(
    ("name", "replies"),
    [
        (
            "gemini-4-hydrogen",
            [
                gemini_reply([gemini_call("bash", {"command": "echo smoke"}, "fc_1", "sig-A")]),
                gemini_reply([gemini_call(REPORT_TOOL, {"report": "smoke"}, "fc_2", "sig-B")]),
            ],
        ),
        (
            "muse-spark-1.3",
            [
                muse_reply(muse_call("bash", {"command": "echo smoke"}), "tool_calls"),
                muse_reply(muse_call(REPORT_TOOL, {"report": "smoke"}), "tool_calls"),
            ],
        ),
    ],
)
def test_the_smoke_round_trip_reports_both_turns(name: str, replies: list[dict[str, Any]]) -> None:
    model = endpoint(name)
    recorder = Recorder(model, replies)
    policy = smoke.smoke_policy(recorder.client, model)
    assert smoke.round_trip(policy) == [
        "turn 1: call bash {'command': 'echo smoke'}",
        "turn 2: report 'smoke'",
    ]
    assert len(recorder.sent) == 2
