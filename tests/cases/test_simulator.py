import json
from pathlib import Path
from typing import Any, cast

import httpx

from steward.cases.case import Case, Person
from steward.cases.simulator import (
    INSTRUCTIONS,
    MAX_TOKENS,
    NO_REPLY,
    CachedSimulator,
    OpenAISimulator,
)

PERSON = Person(name="Dana Reyes", role="owner", description="Dana owns the nightly export.")
CASE = cast(Case, None)  # The simulators do not read the case.
CHAT = ["Agent: Is the export still needed?"]


def reply_body(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "output": [{"type": "message", "content": content}],
        "usage": {"input_tokens": 40, "output_tokens": 7},
    }


def simulator(
    tmp_path: Path, content: list[dict[str, Any]], sent: list[dict[str, Any]]
) -> OpenAISimulator:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=reply_body(content))

    client = httpx.Client(base_url="https://api.test/v1", transport=httpx.MockTransport(handle))
    return OpenAISimulator(client, tmp_path, "gpt-6-luna")


def test_sends_the_same_instructions_and_settings_as_the_claude_simulator(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    sim = simulator(tmp_path, [{"type": "output_text", "text": "Yes, keep it."}], sent)
    assert sim.reply(CASE, PERSON, "Keep the export.", CHAT) == "Yes, keep it."
    (body,) = sent
    assert body["instructions"] == INSTRUCTIONS.format(
        name=PERSON.name,
        description=PERSON.description,
        stance="Keep the export.",
        no_reply=NO_REPLY,
    )
    assert body["input"] == [{"role": "user", "content": CHAT[0]}]
    assert body["max_output_tokens"] == MAX_TOKENS
    assert body["reasoning"] == {"effort": "low"}
    assert body["store"] is False
    assert (sim.input_tokens, sim.output_tokens) == (40, 7)


def test_a_cached_reply_is_not_asked_again(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    sim = simulator(tmp_path, [{"type": "output_text", "text": "Yes."}], sent)
    sim.reply(CASE, PERSON, "Keep it.", CHAT)
    sim.reply(CASE, PERSON, "Keep it.", CHAT)
    assert len(sent) == 1


def test_the_cache_is_kept_apart_per_model(tmp_path: Path) -> None:
    class Fixed(CachedSimulator):
        def _ask(self, system: str, chat: list[str]) -> str:
            return "From the other model."

    Fixed(tmp_path, "claude-haiku-5-5").reply(CASE, PERSON, "Keep it.", CHAT)
    sent: list[dict[str, Any]] = []
    sim = simulator(tmp_path, [{"type": "output_text", "text": "From luna."}], sent)
    assert sim.reply(CASE, PERSON, "Keep it.", CHAT) == "From luna."
    assert len(sent) == 1


def test_no_reply_refusal_and_signature(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    quiet = simulator(tmp_path / "a", [{"type": "output_text", "text": NO_REPLY}], sent)
    assert quiet.reply(CASE, PERSON, "Keep it.", CHAT) is None
    refused = simulator(tmp_path / "b", [{"type": "refusal", "refusal": "No."}], sent)
    assert refused.reply(CASE, PERSON, "Keep it.", CHAT) is None
    signed = simulator(
        tmp_path / "c", [{"type": "output_text", "text": "Dana Reyes: Keep it."}], sent
    )
    assert signed.reply(CASE, PERSON, "Keep it.", CHAT) == "Keep it."
