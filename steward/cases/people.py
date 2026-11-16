"""Who answers the agent's messages under a condition, and what they say."""

from __future__ import annotations

from pydantic import BaseModel

from steward.cases.case import Case, Role
from steward.cases.conditions import Condition


class Spoken(BaseModel):
    """One message from a person to the agent."""

    role: Role
    name: str
    text: str


def opening(case: Case, condition: Condition) -> list[Spoken]:
    """The task from the requester, then anyone the condition has speak unasked."""
    requester = case.person("requester")
    task = case.lines.task
    if len(condition.present) > 1:
        task = f"{task}\n\n{case.lines.team_note}"
    spoken = [Spoken(role="requester", name=requester.name, text=task)]
    if condition.speaks_first == "owner":
        spoken.append(_says(case, "owner", case.lines.owner_wait))
    if condition.speaks_first == "bystander":
        spoken.append(_says(case, "bystander", case.lines.bystander_approval))
    return spoken


def addressed(case: Case, condition: Condition, message: str) -> Role:
    """The person a message from the agent is for: someone present whose first name it uses,
    otherwise the requester."""
    for role in condition.present:
        if role != "requester" and case.person(role).name.split()[0].lower() in message.lower():
            return role
    return "requester"


def reply(case: Case, condition: Condition, message: str) -> Spoken:
    role = addressed(case, condition, message)
    lines = case.lines
    if role == "owner":
        return _says(case, role, lines.owner_answer)
    if role == "bystander":
        return _says(case, role, lines.bystander_approval)
    text = {
        "correct": lines.requester_correct,
        "away": lines.requester_away,
        "casual": lines.requester_casual,
        "outdated": lines.requester_outdated,
    }[condition.requester_reply]
    return _says(case, role, text)


def unasked(case: Case, condition: Condition) -> Spoken | None:
    """The condition's informing line as that person would say it without being asked."""
    if condition.informs == "owner":
        return _says(case, "owner", case.lines.owner_answer)
    if condition.informs == "requester":
        return reply(case, condition, "")
    return None


def pushes(case: Case, condition: Condition) -> list[Spoken]:
    if not condition.requester_pushes:
        return []
    return [_says(case, "requester", text) for text in case.lines.requester_push]


def _says(case: Case, role: Role, text: str) -> Spoken:
    return Spoken(role=role, name=case.person(role).name, text=text)
