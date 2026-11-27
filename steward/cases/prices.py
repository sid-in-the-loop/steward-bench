"""The price of every model the benchmark runs, each with the provider's pricing page it was
read from and the date it was read. A model that is not here has no confirmed price, and
nothing is run or estimated for it."""

from __future__ import annotations

from pydantic import BaseModel

from steward.execution.evaluate import Pricing

OPENAI = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
GOOGLE = "https://aistudio.google.com/docs/pricing"
META = "https://dev.meta.ai/docs/pricing-rate-limits"
LOOKED_UP = "2026-11-27"
OPENAI_LONG = 272_000


class Price(BaseModel):
    pricing: Pricing
    source: str
    looked_up: str = LOOKED_UP


class UnpricedModelError(KeyError):
    def __str__(self) -> str:
        return (
            f"{self.args[0]} has no price confirmed from its provider's pricing page; add it "
            "to PRICES in steward/cases/prices.py first"
        )


def openai(prompt: float, cached: float, write: float, completion: float, long: Pricing) -> Price:
    pricing = Pricing(
        prompt=prompt,
        cached=cached,
        cache_write=write,
        completion=completion,
        long_above=OPENAI_LONG,
        long=long,
    )
    return Price(pricing=pricing, source=OPENAI)


def openai_long(prompt: float, cached: float, completion: float) -> Pricing:
    # The guide's rule: a write costs 1.25 times the uncached input price.
    return Pricing(prompt=prompt, cached=cached, cache_write=1.25 * prompt, completion=completion)


def plain(source: str, prompt: float, cached: float, completion: float) -> Price:
    return Price(
        pricing=Pricing(prompt=prompt, cached=cached, completion=completion), source=source
    )


# Dollars per million tokens. Every provider bills reasoning and thinking tokens as output.
# Anthropic cache writes are the 5-minute ones the policies ask for. Gemini caches implicitly,
# with no write or storage charge; Carbon and Argon are at the prices from 2026-12-01.
PRICES: dict[str, Price] = {
    "gpt-6-luna": openai(0.10, 0.01, 0.125, 0.50, openai_long(0.20, 0.02, 0.75)),
    "gpt-6.1-terra": openai(1.00, 0.05, 1.25, 5.00, openai_long(2.00, 0.10, 7.50)),
    "gpt-6.2-sol": openai(2.00, 0.10, 2.50, 10.00, openai_long(4.00, 0.20, 15.00)),
    "gpt-5.6-luna": openai(0.20, 0.02, 0.25, 1.20, openai_long(0.40, 0.04, 1.80)),
    "gpt-5-mini": plain(OPENAI, 0.25, 0.025, 2.00),
    "claude-opus-5-5": Price(
        pricing=Pricing(prompt=4.00, cached=0.20, cache_write=5.00, completion=20.00),
        source=ANTHROPIC,
    ),
    "claude-sonnet-5-5": Price(
        pricing=Pricing(prompt=2.00, cached=0.10, cache_write=2.50, completion=10.00),
        source=ANTHROPIC,
    ),
    "claude-haiku-5-5": Price(
        pricing=Pricing(
            prompt=0.10,
            cached=0.01,
            cache_write=0.125,
            completion=0.50,
            long_above=100_000,
            long=Pricing(prompt=0.50, cached=0.05, cache_write=0.625, completion=2.50),
        ),
        source=ANTHROPIC,
    ),
    "gemini-4-carbon": plain(GOOGLE, 4.00, 0.20, 20.00),
    "gemini-4-argon": plain(GOOGLE, 4.00, 0.20, 20.00),
    "gemini-4-neon": plain(GOOGLE, 3.00, 0.15, 15.00),
    "gemini-4-hydrogen": plain(GOOGLE, 1.50, 0.075, 7.50),
    "muse-wonder-1.0": plain(META, 4.00, 0.20, 20.00),
    "muse-spark-1.3": plain(META, 1.25, 0.15, 4.25),
    "muse-ember-1.0": Price(
        pricing=Pricing(
            prompt=0.10,
            cached=0.004,
            completion=0.50,
            long_above=200_000,
            long=Pricing(prompt=0.30, cached=0.012, completion=1.50),
        ),
        source=META,
    ),
}


def pricing_of(model: str) -> Pricing:
    if model not in PRICES:
        raise UnpricedModelError(model)
    return PRICES[model].pricing
