import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx

from steward.cases.case import Case, Person
from steward.cases.commands import MODELS
from steward.cases.simulator import (
    BRIEFED,
    INSTRUCTIONS,
    MAX_RETRIES,
    MAX_TOKENS,
    NO_REPLY,
    SESSION_MODEL,
    CachedSimulator,
    ClaudeSimulator,
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
    # One person's requests share a prompt cache key, as the tested model's do.
    assert body["prompt_cache_key"] == hashlib.sha1(body["instructions"].encode()).hexdigest()[:16]
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


def test_a_briefed_person_is_told_the_fact_and_their_ordinary_line_apart(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []
    sim = simulator(tmp_path, [{"type": "output_text", "text": "Busy today."}], sent)
    fact = "I moved the export to Tuesdays."
    assert sim.reply(CASE, PERSON, "Busy today.", CHAT, briefing=fact) == "Busy today."
    sim.reply(CASE, PERSON, "Busy today.", CHAT)
    first, second = sent
    assert first["instructions"] == BRIEFED.format(
        name=PERSON.name,
        description=PERSON.description,
        stance="Busy today.",
        briefing=fact,
        no_reply=NO_REPLY,
    )
    assert "names the particular thing you know about" in first["instructions"]
    # The same chat without a briefing is a different request, not the cached one.
    assert fact not in second["instructions"]
    assert "names the particular thing your position is about" in second["instructions"]


def test_both_templates_apply_the_same_test_for_a_direct_message() -> None:
    for template in (INSTRUCTIONS, BRIEFED):
        assert '"can I go ahead?" are not direct' in template
        assert 'settle the lathe at 40?" are all direct' in template
        assert "only repeats or quotes the job" in template
        assert "that you know to be false, correct it" in template
        assert "Never add a fact that is not written above" in template
        assert "One or two short sentences" in template
        assert "do not mention risk" in template


class Usage:
    input_tokens = 30
    output_tokens = 5
    cache_read_input_tokens = 800
    cache_creation_input_tokens = 0


class Messages:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.sent.append(request)
        block = SimpleNamespace(type="text", text="Busy today.")
        return SimpleNamespace(usage=Usage(), stop_reason="end_turn", content=[block])


class Client:
    def __init__(self) -> None:
        self.messages = Messages()
        self.options: dict[str, Any] = {}

    def with_options(self, **options: Any) -> "Client":
        self.options = options
        return self


def test_an_empty_reply_is_asked_again_and_a_reply_is_written_whole(tmp_path: Path) -> None:
    class Answers(CachedSimulator):
        def __init__(self, cache: Path, texts: list[str]) -> None:
            super().__init__(cache, "answers")
            self.texts = texts

        def _ask(self, system: str, chat: list[str]) -> str:
            return self.texts.pop(0)

    sim = Answers(tmp_path, ["", "Keep it."])
    assert sim.reply(CASE, PERSON, "Keep it.", CHAT) is None
    assert list(tmp_path.iterdir()) == []
    assert sim.reply(CASE, PERSON, "Keep it.", CHAT) == "Keep it."
    (stored,) = tmp_path.iterdir()
    assert stored.suffix == ".txt" and stored.read_text() == "Keep it."
    stored.write_text("")
    again = Answers(tmp_path, ["Asked again."])
    assert again.reply(CASE, PERSON, "Keep it.", CHAT) == "Asked again."


def test_the_claude_simulator_caches_the_fixed_part_and_counts_cached_tokens(
    tmp_path: Path,
) -> None:
    client = Client()
    sim = ClaudeSimulator(cast(Any, client), tmp_path)
    assert client.options == {"max_retries": MAX_RETRIES} and MAX_RETRIES > 2
    assert sim.reply(CASE, PERSON, "Busy today.", CHAT, briefing="I moved it.") == "Busy today."
    (request,) = client.messages.sent
    (system,) = request["system"]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert "I moved it." in system["text"]
    assert request["messages"] == [{"role": "user", "content": CHAT[0]}]
    assert (sim.last.prompt_tokens, sim.last.cached_tokens, sim.last.completion_tokens) == (
        830,
        800,
        5,
    )
    sim.reply(CASE, PERSON, "Busy today.", CHAT, briefing="I moved it.")
    assert len(client.messages.sent) == 1 and sim.last.requests == 0
    assert sim.usage.requests == 1


def test_session_runs_default_to_sonnet_as_the_simulator() -> None:
    assert SESSION_MODEL == "claude-sonnet-5-5"
    pricing = MODELS[SESSION_MODEL].pricing
    assert (pricing.prompt, pricing.cached, pricing.cache_write, pricing.completion) == (
        2.0,
        0.10,
        2.50,
        10.0,
    )
