"""The tested models: where each is served, its price, and the policy that runs it."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, Literal

import anthropic
import httpx
from pydantic import BaseModel

from steward.execution.anthropic_policy import MAX_RETRIES, AnthropicPolicy
from steward.execution.gemini_policy import GEMINI_URL, GeminiPolicy
from steward.execution.policy import Dialect, Message, ModelPolicy, Policy, RunSettings
from steward.prices import Pricing, pricing_of
from steward.task import Case

MUSE_URL = "https://api.meta.ai/v1"
# Long enough for a reply that thinks at length; the Gemini 4 guides ask for 20 minutes.
LONG_REQUEST_SECONDS = 1200.0
# The output budget of Muse models, whose default reasoning (up to "max") counts against it.
MUSE_OUTPUT_TOKENS = 16_000


class Model(BaseModel):
    """A tested model and where it is served. `reasoning` is the reasoning setting the
    provider's documentation gives as the model's default; no request sets one."""

    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    dialect: Dialect | Literal["anthropic", "gemini"] = "openai"
    pricing: Pricing
    reasoning: str | None = None
    # The output budget of a turn, where it is not the run's own (`RunSettings`).
    output_tokens: int | None = None
    timeout_seconds: float = 300.0

    def auth_headers(self, key: str) -> dict[str, str]:
        """The header that carries the API key: Gemini's own, otherwise a bearer token."""
        if self.dialect == "gemini":
            return {"x-goog-api-key": key}
        return {"Authorization": f"Bearer {key}"}


class ReasoningPolicy(ModelPolicy):
    """A model policy that also asks for a summary of the model's reasoning and keeps the one
    from the latest turn. A model that rejects the request is run without it."""

    thought: str | None = None

    def request_body(self, messages: list[Message]) -> dict[str, Any]:
        body = super().request_body(messages)
        if self.dialect == "openai" and "reasoning" not in self.dropped:
            body["reasoning"] = {"summary": "auto"}
        return body

    def post(self, messages: list[Message]) -> dict[str, Any]:
        try:
            body = super().post(messages)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 400 or "reasoning" not in error.response.text:
                raise
            self.dropped.append("reasoning")
            body = super().post(messages)
        parts = [
            part.get("text") or ""
            for item in body.get("output") or []
            if item.get("type") == "reasoning"
            for part in item.get("summary") or []
        ]
        self.thought = "\n\n".join(p for p in parts if p) or None
        return body


PolicyFor = Callable[[list[Message]], Policy]


def model_policy(client: httpx.Client, model: Model, case: Case) -> PolicyFor:
    if model.dialect == "anthropic":
        # A timeout of its own: with the SDK's default one it refuses, before sending, any
        # request whose output budget could take more than ten minutes, as the retry's does.
        claude = anthropic.Anthropic(max_retries=MAX_RETRIES, timeout=LONG_REQUEST_SECONDS)
        return lambda tools: AnthropicPolicy(claude, model.name, tools)
    if model.dialect == "gemini":
        return lambda tools: GeminiPolicy(client, model.name, tools)
    dialect = model.dialect
    key = hashlib.sha1(case.id.encode()).hexdigest()[:16]
    settings = RunSettings()
    if model.output_tokens is not None:
        settings = RunSettings(max_tokens_per_turn=model.output_tokens)
    return lambda tools: ReasoningPolicy(
        client, model.name, tools, settings, dialect=dialect, cache_key=key
    )


def claude(name: str, reasoning: str) -> Model:
    return Model(
        name=name,
        dialect="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        pricing=pricing_of(name),
        reasoning=reasoning,
    )


def gemini(name: str, reasoning: str) -> Model:
    return Model(
        name=name,
        base_url=GEMINI_URL,
        api_key_env="GEMINI_API_KEY",
        dialect="gemini",
        pricing=pricing_of(name),
        reasoning=reasoning,
        timeout_seconds=LONG_REQUEST_SECONDS,
    )


def muse(name: str, reasoning: str) -> Model:
    """A Muse model on Meta's Chat Completions endpoint, which carries no reasoning between
    turns and so needs nothing sent back but the conversation."""
    return Model(
        name=name,
        base_url=MUSE_URL,
        api_key_env="MUSE_API_KEY",
        dialect="openrouter",
        pricing=pricing_of(name),
        reasoning=reasoning,
        output_tokens=MUSE_OUTPUT_TOKENS,
        timeout_seconds=LONG_REQUEST_SECONDS,
    )


# The models with an endpoint set up here, at their prices in PRICES. Each runs at its
# provider's default reasoning, given as the provider's documentation states it: Claude's
# effort (https://platform.claude.com/docs/en/build-with-claude/effort), Gemini's thinking
# level (https://aistudio.google.com/docs/thinking?generate-content=true#thinking-levels) and
# Muse's reasoning effort (https://dev.meta.ai/docs/protocols/chat-completions). The model
# names are the ids each API expects. claude-sonnet-5-5 plays people in session runs.
MODELS = (
    {
        name: Model(name=name, pricing=pricing_of(name))
        for name in ["gpt-6-luna", "gpt-5.6-luna", "gpt-5-mini", "gpt-6.2-sol", "gpt-6.1-terra"]
    }
    | {
        "claude-haiku-5-5": claude("claude-haiku-5-5", "medium"),
        "claude-sonnet-5-5": claude("claude-sonnet-5-5", "high"),
        "claude-opus-5-5": claude("claude-opus-5-5", "medium"),
    }
    | {
        "gemini-4-carbon": gemini("gemini-4-carbon", "medium"),
        "gemini-4-argon": gemini("gemini-4-argon", "medium"),
        "gemini-4-neon": gemini("gemini-4-neon", "high"),
        "gemini-4-hydrogen": gemini("gemini-4-hydrogen", "high"),
    }
    | {
        "muse-wonder-1.0": muse("muse-wonder-1.0", "max"),
        "muse-spark-1.3": muse("muse-spark-1.3", "high"),
        "muse-ember-1.0": muse("muse-ember-1.0", "max"),
    }
)


class UnknownModelError(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


def endpoint(name: str) -> Model:
    """The model to run, or a refusal that says why it cannot be run."""
    pricing_of(name)
    if name not in MODELS:
        raise UnknownModelError(
            f"{name} has a price but no endpoint set up in MODELS in steward/models.py"
        )
    return MODELS[name]
