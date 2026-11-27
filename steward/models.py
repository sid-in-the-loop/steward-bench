"""The tested models: where each is served, its price, and the policy that runs it."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, Literal

import anthropic
import httpx
from pydantic import BaseModel

from steward.execution.anthropic_policy import MAX_RETRIES, AnthropicPolicy
from steward.execution.policy import Dialect, Message, ModelPolicy, Policy
from steward.prices import Pricing, pricing_of
from steward.task import Case


class Model(BaseModel):
    """A tested model and where it is served."""

    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    dialect: Dialect | Literal["anthropic"] = "openai"
    pricing: Pricing


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
        claude = anthropic.Anthropic(max_retries=MAX_RETRIES)
        return lambda tools: AnthropicPolicy(claude, model.name, tools)
    dialect = model.dialect
    key = hashlib.sha1(case.id.encode()).hexdigest()[:16]
    return lambda tools: ReasoningPolicy(client, model.name, tools, dialect=dialect, cache_key=key)


# The models with an endpoint set up here, at their prices in PRICES. claude-sonnet-5-5 plays
# people in session runs and is not run as an agent from this machine.
MODELS = {
    name: Model(name=name, pricing=pricing_of(name))
    for name in ["gpt-6-luna", "gpt-5.6-luna", "gpt-5-mini", "gpt-6.2-sol", "gpt-6.1-terra"]
} | {
    name: Model(
        name=name, dialect="anthropic", api_key_env="ANTHROPIC_API_KEY", pricing=pricing_of(name)
    )
    for name in ["claude-haiku-5-5", "claude-sonnet-5-5"]
}


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
