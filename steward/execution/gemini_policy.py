"""A model policy for Gemini models on the Gemini API's native generateContent endpoint.

Gemini 4 models attach a thought signature to the first function call of each reply and
refuse a request whose current turn has a function call without its signature. So, as for
Claude, this policy keeps each reply's parts and sends those back unchanged in place of the
session's record of the same turn. A function call the model did not write in this policy,
such as one of the inherited history, is sent with the documented placeholder signature when
it falls in the current turn, and with none before it, where signatures are not checked.

Docs: https://aistudio.google.com/docs/thought-signatures?generate-content=true and
https://aistudio.google.com/api-reference/generate-content.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from steward.execution.anthropic_policy import NO_REPLY, REPORT_RECEIVED, tool_use_id
from steward.execution.history import REPORT_TOOL
from steward.execution.policy import Message, ModelPolicy, Turn, Usage, post_with_retry
from steward.execution.record import Action

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
# The per-request output cap of a non-streaming call on Gemini 4 Carbon and Argon; Neon and
# Hydrogen allow 65,536. Thinking counts against it, and the guide asks for at least this.
MAX_OUTPUT_TOKENS = 32_768
# Accepted in place of a thought signature on a function call the model did not write.
PLACEHOLDER_SIGNATURE = "skip_thought_signature_validator"
# A reply that finished for any other reason is not taken as the agent's turn.
FINISHED = ("STOP", None)


def gemini_tools(tools: list[Message]) -> list[dict[str, Any]]:
    """Tool definitions as function declarations. Schemas go in `parametersJsonSchema`, as
    the recorded tools' empty property schemas are JSON Schema but not OpenAPI."""
    declarations = []
    for tool in tools:
        function = tool["function"]
        declarations.append(
            {
                "name": function["name"],
                "description": function.get("description") or "",
                "parametersJsonSchema": function.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    return [{"functionDeclarations": declarations}]


def first_call(parts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((p["functionCall"] for p in parts if "functionCall" in p), None)


def kept_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A reply's parts up to its first function call. The session runs one action a turn,
    and a call sent back without its response is refused; the signature is on the first."""
    kept = []
    for part in parts:
        kept.append(part)
        if "functionCall" in part:
            break
    return kept


def text_of(parts: list[dict[str, Any]]) -> str:
    return "".join(p.get("text") or "" for p in parts if not p.get("thought"))


class GeminiPolicy:
    """Asks a Gemini model for the next turn. A call to the report tool ends the run, any
    other tool call is an action, and plain text is a message to the principal. Thinking is
    left at the model's default level and its summary is kept as the turn's thought."""

    thought: str | None = None

    def __init__(
        self,
        client: httpx.Client,
        model: str,
        tools: list[Message],
        output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.tools = gemini_tools(tools)
        self.output_tokens = output_tokens
        self.usage = Usage()
        # The kept parts of each reply, by the position its turn takes in the session.
        self.replies: dict[int, list[dict[str, Any]]] = {}

    def _conversation(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """The system text and the contents. A turn this policy produced is sent back as the
        parts it came with; every function call is followed by its response, and a report by
        a short acknowledgement."""
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        # The calls still to be answered, as id and name, in the order they were made.
        owed: list[tuple[str | None, str]] = []

        def add(role: str, parts: list[dict[str, Any]]) -> None:
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": list(parts)})

        def respond(call: tuple[str | None, str], output: str) -> None:
            response: dict[str, Any] = {"name": call[1], "response": {"output": output}}
            if call[0]:
                response["id"] = call[0]
            add("user", [{"functionResponse": response}])

        def settle() -> None:
            while owed:
                respond(owed.pop(0), REPORT_RECEIVED)

        wire = ModelPolicy.wire_messages(messages)
        for index, (message, entry) in enumerate(zip(messages, wire, strict=True)):
            role = message["role"]
            if role == "system":
                system.append(str(message.get("content") or ""))
                continue
            if role == "tool":
                if owed:
                    own = tool_use_id(str(entry.get("tool_call_id") or ""))
                    at = next((i for i, call in enumerate(owed) if call[0] == own), 0)
                    respond(owed.pop(at), str(message.get("content") or "") or "(no output)")
                continue
            settle()
            if role == "assistant" and index in self.replies:
                parts = self.replies[index]
                add("model", parts)
                call = first_call(parts)
                if call is not None:
                    owed.append((call.get("id"), str(call["name"])))
                continue
            if role == "assistant":
                parts = []
                text = str(entry.get("content") or "")
                if text:
                    parts.append({"text": text})
                for call_entry in entry.get("tool_calls") or []:
                    function = call_entry["function"]
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    call_id = tool_use_id(str(call_entry["id"]))
                    owed.append((call_id, str(function["name"])))
                    parts.append(
                        {
                            "functionCall": {
                                "id": call_id,
                                "name": function["name"],
                                "args": arguments if isinstance(arguments, dict) else {},
                            }
                        }
                    )
                add("model", parts or [{"text": "(no text)"}])
                continue
            add("user", [{"text": str(message.get("content") or "") or "(empty)"}])
        settle()
        if contents and contents[-1]["role"] == "model":
            add("user", [{"text": NO_REPLY}])
        sign_current_turn(contents)
        return "\n\n".join(part for part in system if part), contents

    def request_body(self, messages: list[Message]) -> dict[str, Any]:
        system, contents = self._conversation(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "tools": self.tools,
            # Thought summaries only; the thinking level stays at the model's default.
            "generationConfig": {
                "maxOutputTokens": self.output_tokens,
                "thinkingConfig": {"includeThoughts": True},
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        path = f"/models/{self.model}:generateContent"
        reply = post_with_retry(self.client, body, path=path, field="usageMetadata")
        self._count(reply.get("usageMetadata") or {})
        return reply

    @staticmethod
    def parts_of(reply: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = reply.get("candidates") or [{}]
        return list((candidates[0].get("content") or {}).get("parts") or [])

    @classmethod
    def unusable(cls, reply: dict[str, Any]) -> str | None:
        """Why a reply with no function call cannot be taken as the agent's turn: a blocked
        prompt, a reply that stopped for any reason but its natural end, or one with no text.
        None for a usable reply."""
        parts = cls.parts_of(reply)
        if first_call(parts) is not None:
            return None
        blocked = (reply.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            return f"blocked: {blocked}"
        reason = ((reply.get("candidates") or [{}])[0]).get("finishReason")
        if reason not in FINISHED:
            return f"stopped: {reason}"
        if not text_of(parts).strip():
            return "no text and no tool call"
        return None

    def next_turn(self, messages: list[Message]) -> Turn:
        body = self.request_body(messages)
        reply = self.post(body)
        if self.unusable(reply) is not None:
            # Asked once more; a reply still unusable ends the shift.
            reply = self.post(body)
        parts = self.parts_of(reply)
        thoughts = [p.get("text") or "" for p in parts if p.get("thought")]
        self.thought = "\n\n".join(t for t in thoughts if t) or None
        problem = self.unusable(reply)
        if problem is not None:
            # Nothing of it is sent back: the next turn follows the last usable one.
            return Turn(error=problem)
        kept = kept_parts(parts)
        self.replies[len(messages)] = kept
        call = first_call(kept)
        if call is None:
            return Turn(message=text_of(kept))
        arguments = call.get("args")
        arguments = arguments if isinstance(arguments, dict) else {}
        if call["name"] == REPORT_TOOL:
            return Turn(report=str(arguments.get("report", "")))
        return Turn(action=Action(tool=str(call["name"]), arguments=arguments))

    def _count(self, usage: dict[str, Any]) -> None:
        """Prompt tokens include tool-use prompt tokens; output tokens are the reply's and the
        thinking's, which are billed as output."""
        prompt = int(usage.get("promptTokenCount") or 0) + int(
            usage.get("toolUsePromptTokenCount") or 0
        )
        self.usage.requests += 1
        self.usage.prompt_tokens += prompt
        self.usage.largest_prompt_tokens = max(self.usage.largest_prompt_tokens, prompt)
        self.usage.cached_tokens += int(usage.get("cachedContentTokenCount") or 0)
        self.usage.completion_tokens += int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )


def sign_current_turn(contents: list[dict[str, Any]]) -> None:
    """Give the placeholder signature to every unsigned first function call of a step in
    the current turn: the contents after the last user content with text in it."""
    start = max(
        (
            i
            for i, content in enumerate(contents)
            if content["role"] == "user" and any("text" in p for p in content["parts"])
        ),
        default=-1,
    )
    for content in contents[start + 1 :]:
        if content["role"] != "model":
            continue
        parts = content["parts"]
        at = next((i for i, part in enumerate(parts) if "functionCall" in part), None)
        if at is not None and "thoughtSignature" not in parts[at]:
            # A copy, so that a kept reply is never changed.
            parts[at] = {**parts[at], "thoughtSignature": PLACEHOLDER_SIGNATURE}
