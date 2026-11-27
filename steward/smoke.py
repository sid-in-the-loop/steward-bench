"""One minimal tool-call round trip per model, through the same policy a session uses, to
confirm an endpoint, its tool calls across turns and the usage it reports. It prints an upper
bound on its cost first and calls nothing without --confirm.

    scripts/smoke-models [--model M ...] [--confirm]
"""

from __future__ import annotations

import argparse
import os
import sys

import anthropic
import httpx

from steward.execution.anthropic_policy import MAX_RETRIES, AnthropicPolicy
from steward.execution.gemini_policy import GeminiPolicy
from steward.execution.history import REPORT_TOOL, function_tool
from steward.execution.policy import Message, Policy, RunSettings, Usage
from steward.models import LONG_REQUEST_SECONDS, Model, ReasoningPolicy, endpoint

NEW_MODELS = [
    "claude-opus-5-5",
    "gemini-4-carbon",
    "gemini-4-argon",
    "gemini-4-neon",
    "gemini-4-hydrogen",
    "muse-wonder-1.0",
    "muse-spark-1.3",
    "muse-ember-1.0",
]
# The output budget of each request; a retry of an unusable reply may get twice as much.
OUTPUT_TOKENS = 4096
# An assumed ceiling on the input of one request: the prompt, two tools and one result.
INPUT_TOKENS = 3000
# Two turns, each of a request and at most one retry.
REQUESTS = 4
TOOLS: list[Message] = [
    function_tool(
        "bash",
        "Run a shell command.",
        {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    ),
    function_tool(
        REPORT_TOOL,
        "Finish the task and report the outcome to the user.",
        {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]},
    ),
]
OPENING: list[Message] = [
    {"role": "system", "content": "You are checking that a tool connection works."},
    {
        "role": "user",
        "content": "Run `echo smoke` with the bash tool, then report what it printed.",
    },
]
RESULT = "smoke"


def bound_usd(model: Model) -> float:
    """The most the smoke can cost the model: every request at the input ceiling and every
    turn retried with twice the output budget, at the full input price."""
    price = model.pricing
    output = (REQUESTS // 2) * 3 * OUTPUT_TOKENS
    return (REQUESTS * INPUT_TOKENS * price.prompt + output * price.completion) / 1_000_000


def smoke_policy(client: httpx.Client, model: Model) -> Policy:
    if model.dialect == "anthropic":
        claude = anthropic.Anthropic(max_retries=MAX_RETRIES, timeout=LONG_REQUEST_SECONDS)
        return AnthropicPolicy(claude, model.name, TOOLS, max_tokens=OUTPUT_TOKENS)
    if model.dialect == "gemini":
        return GeminiPolicy(client, model.name, TOOLS, output_tokens=OUTPUT_TOKENS)
    settings = RunSettings(max_tokens_per_turn=OUTPUT_TOKENS)
    return ReasoningPolicy(client, model.name, TOOLS, settings, dialect=model.dialect)


def round_trip(policy: Policy) -> list[str]:
    """Ask for a tool call, answer it, and ask for the next turn. What each turn was."""
    messages = list(OPENING)
    first = policy.next_turn(messages)
    seen = [f"turn 1: {describe(first)}"]
    if first.action is None:
        return [*seen, "no tool call, so no round trip"]
    messages.append({"role": "assistant", "content": "", "action": first.action.model_dump()})
    messages.append({"role": "tool", "content": RESULT})
    second = policy.next_turn(messages)
    return [*seen, f"turn 2: {describe(second)}"]


def describe(turn: object) -> str:
    action = getattr(turn, "action", None)
    if action is not None:
        return f"call {action.tool} {action.arguments}"
    for kind in ("report", "message", "error"):
        value = getattr(turn, kind, None)
        if value is not None:
            return f"{kind} {str(value)[:120]!r}"
    return "nothing"


def usage_line(model: Model, usage: Usage) -> str:
    return (
        f"{usage.requests} requests, {usage.prompt_tokens} input tokens "
        f"({usage.cached_tokens} cached), {usage.completion_tokens} output tokens, "
        f"${model.pricing.cost(usage):.4f}"
    )


def run(model: Model) -> list[str]:
    client = httpx.Client(
        base_url=model.base_url,
        headers=model.auth_headers(os.environ.get(model.api_key_env, "")),
        timeout=httpx.Timeout(model.timeout_seconds, connect=30.0),
    )
    policy = smoke_policy(client, model)
    try:
        lines = round_trip(policy)
    except Exception as error:  # noqa: BLE001 - one model's failure must not stop the rest
        response = getattr(error, "response", None)
        body = response.text[:500] if isinstance(response, httpx.Response) else ""
        lines = [f"failed: {type(error).__name__}: {str(error)[:300]} {body}"]
    finally:
        client.close()
    usage = getattr(policy, "usage", Usage())
    thought = "a thought summary" if getattr(policy, "thought", None) else "no thought summary"
    return [*lines, f"{usage_line(model, usage)}; {thought}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One tool-call round trip per model.")
    parser.add_argument("--model", action="append", help="a model to try; all new ones if none")
    parser.add_argument("--confirm", action="store_true", help="make the calls")
    args = parser.parse_args(argv)
    try:
        models = [endpoint(name) for name in args.model or NEW_MODELS]
    except KeyError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    for model in models:
        print(f"{model.name}: at most ${bound_usd(model):.2f}")
    print(f"at most ${sum(bound_usd(m) for m in models):.2f} in all")
    if not args.confirm:
        print("Add --confirm to run.")
        return 0
    for model in models:
        for line in run(model):
            print(f"{model.name}: {line}", flush=True)
    return 0
