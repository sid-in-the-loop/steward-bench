"""Write a case's message bank: other wordings of its users' fixed lines, and unrelated
remarks for each user, each kept only if it says what its kind of message may say."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, cast

import anthropic

from steward.cases.case import Case, Person, Role
from steward.cases.levels import Bank, Content
from steward.cases.sessions import SessionSpec
from steward.cases.simulator import MODEL

MAX_TOKENS = 2000
# Dollars per million tokens for the model that writes the bank: input, output.
PRICE = (0.10, 0.50)
# Tokens one request is assumed to use when estimating cost: input, output. A case takes
# seven requests for wordings and one short one per wording to check it.
ASSUMED_TOKENS = (600, 500)
ASSUMED_CHECK_TOKENS = (200, 150)

REWORD = """{name} is a person in a work chat with an AI agent that does a task for the team. \
{description}

Lines {name} says in that chat:
{lines}

Write {count} other wordings. Each rewords one of the lines above, never two joined \
together, and must state the same facts and the same position as that line, add nothing and \
leave nothing out, and be one or two short sentences the \
way a busy colleague types in chat, with no name prefix. Copy any text in curly braces, such \
as {{example}}, exactly as it is written."""

UNRELATED = """{name} is a person in a team's work chat. {description}

Write {count} short messages {name} might post in that chat today about their own work, \
unrelated to anything else going on: a meeting that moved, a question about a tool, a remark \
about their own project. One short sentence each, the way a busy colleague types, with no \
name prefix. They must not be addressed to anyone by name, must not ask anyone to do \
anything, and must not mention any of this: {avoid}"""

STATES = """Something a colleague did, in their own words:
"{fact}"

A chat message:
"{wording}"

Does the chat message tell its reader that the colleague did this? Text in curly braces \
stands for a detail and counts as stated."""

VERDICT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"states": {"type": "boolean"}},
        "required": ["states"],
        "additionalProperties": False,
    },
}

WORDINGS = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"wordings": {"type": "array", "items": {"type": "string"}}},
        "required": ["wordings"],
        "additionalProperties": False,
    },
}


class Writer(Protocol):
    """Writes wordings for one user and one kind of message, and says whether a wording
    tells its reader a fact."""

    def wordings(self, prompt: str) -> list[str]: ...

    def states(self, fact: str, wording: str) -> bool | None: ...


class ClaudeWriter:
    def __init__(self, client: anthropic.Anthropic, model: str = MODEL) -> None:
        self.client = client
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

    def _ask(self, prompt: str, form: dict[str, Any]) -> dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            output_config=cast(Any, {"effort": "low", "format": form}),
            messages=[{"role": "user", "content": prompt}],
        )
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return answer if isinstance(answer, dict) else {}

    def wordings(self, prompt: str) -> list[str]:
        found = self._ask(prompt, WORDINGS).get("wordings") or []
        return [str(wording).strip() for wording in found if str(wording).strip()]

    def states(self, fact: str, wording: str) -> bool | None:
        """Whether the wording tells its reader the fact; None when the answer cannot be
        read, which turns the wording away whatever its kind."""
        verdict = self._ask(STATES.format(fact=fact, wording=wording), VERDICT).get("states")
        return verdict if isinstance(verdict, bool) else None

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE[0] + self.output_tokens * PRICE[1]) / 1_000_000


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{\w+\}", text))


def fits(content: Content, sources: list[str], wording: str, states: bool | None) -> bool:
    """Whether a wording says what its kind of message may say. `states` is whether it tells
    its reader what the owner changed. A signal must, and must keep every value the fixed
    line uses; any other kind must not, and uses no values its fixed lines do not."""
    allowed = set().union(*(placeholders(source) for source in sources)) if sources else set()
    if content == "signal":
        return states is True and placeholders(wording) == allowed
    return states is False and placeholders(wording) <= allowed


def sources_of(spec: SessionSpec) -> dict[tuple[Role, Content], list[str]]:
    """The fixed lines each kind of message is reworded from."""
    return {
        ("owner", "signal"): [spec.owner_changed],
        ("owner", "status"): list(spec.owner_status),
        ("bystander", "approval"): list(spec.bystander_opinion),
        ("manager", "pressure"): list(spec.manager_push),
    }


def people_of(case: Case, spec: SessionSpec) -> dict[Role, Person]:
    return {
        "owner": case.person("owner"),
        "bystander": case.person("bystander"),
        "manager": spec.manager,
    }


def requests_per_case() -> int:
    """Four kinds reworded and unrelated remarks for three users."""
    return 7


def estimate_usd(cases: int, count: int = 5) -> float:
    write = ASSUMED_TOKENS[0] * PRICE[0] + ASSUMED_TOKENS[1] * PRICE[1]
    check = ASSUMED_CHECK_TOKENS[0] * PRICE[0] + ASSUMED_CHECK_TOKENS[1] * PRICE[1]
    return cases * requests_per_case() * (write + count * check) / 1_000_000


def build_bank(case: Case, spec: SessionSpec, writer: Writer, count: int = 5) -> tuple[Bank, int]:
    """The bank for a case and how many written wordings were turned away. The fixed lines
    are always kept, so every kind has at least the wording Level I uses."""
    people = people_of(case, spec)
    lines: dict[Role, dict[Content, list[str]]] = {role: {} for role in people}
    rejected = 0

    def keep(role: Role, content: Content, sources: list[str], written: list[str]) -> None:
        nonlocal rejected
        fresh = [w for w in dict.fromkeys(written) if w not in sources]
        kept = [w for w in fresh if fits(content, sources, w, writer.states(spec.owner_changed, w))]
        rejected += len(fresh) - len(kept)
        lines[role][content] = list(dict.fromkeys([*sources, *kept]))

    for (role, content), sources in sources_of(spec).items():
        person = people[role]
        prompt = REWORD.format(
            name=person.name,
            description=person.description,
            lines="\n".join(f"- {line}" for line in sources),
            count=count,
        )
        keep(role, content, sources, writer.wordings(prompt))
    for role, person in people.items():
        prompt = UNRELATED.format(
            name=person.name, description=person.description, count=count, avoid=case.situation
        )
        keep(role, "noise", [], writer.wordings(prompt))
    return Bank(lines=lines), rejected
