"""Write the run viewer: one page holding the cases, their inherited sessions and the runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from steward.cases import sandbox
from steward.cases.case import DOMAIN_NAMES, Case, Repo
from steward.cases.commands import MODELS, current_runs, load_cases
from steward.cases.conditions import CONDITIONS
from steward.cases.run import built_directory
from steward.config import Settings
from steward.filter.selection import load_by_ids
from steward.schema import Step

TEMPLATE = Path(__file__).with_name("viewer.html")
TEXT_LIMIT = 900
MARK = "/*DATA*/null/*END*/"


def clipped(text: str) -> str:
    if len(text) <= TEXT_LIMIT:
        return text
    return f"{text[:TEXT_LIMIT]}\n[{len(text) - TEXT_LIMIT} more characters not shown]"


def slim(event: dict[str, Any]) -> dict[str, Any]:
    """An event without its empty fields and with long text clipped, to keep the page small."""
    kept = {key: value for key, value in event.items() if value not in (None, False, "")}
    kept["text"] = clipped(event["text"])
    if kept.get("thought"):
        kept["thought"] = clipped(kept["thought"])
    return kept


def inherited(settings: Settings, case: Case) -> list[dict[str, Any]]:
    """The session the agent inherits, as the viewer shows it."""
    environment = case.environment
    steps: list[Step]
    if isinstance(environment, Repo):
        steps = sandbox.load(built_directory(settings, case))[1]
    else:
        trajectory = next(iter(load_by_ids(settings, [environment.trajectory_id])))
        steps = list(trajectory.steps[: environment.takeover_step])
    who = {"user": "requester", "agent_text": "previous", "action": "previous"}
    shown = [
        {
            "who": who.get(step.kind, "environment"),
            "kind": {"action": "call", "result": "result"}.get(step.kind, "message"),
            "tool": step.tool,
            "text": clipped(step.content),
        }
        for step in steps
        if step.kind != "system"
    ]
    for item in case.history if not isinstance(environment, Repo) else []:
        text = item.user or item.agent or ""
        role = "requester" if item.user is not None else "previous"
        shown.append({"who": role, "kind": "message", "tool": None, "text": text})
    return shown


def case_card(settings: Settings, case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "group": case.family or DOMAIN_NAMES[case.domain],
        "domain": case.domain,
        "title": case.title,
        "people": [p.model_dump() for p in case.people],
        "situation": case.situation,
        "harmful_action": case.harmful_action,
        "safe_path": case.safe_path,
        "harm_statement": case.harm_statement,
        "twin": case.harmful_reference is None,
        "task": case.lines.task,
        "history": inherited(settings, case),
    }


def page_data(
    settings: Settings, models: list[str] | None, only: list[str] | None, plain: bool = False
) -> dict[str, Any]:
    cases = load_cases(settings, only)
    runs: list[dict[str, Any]] = []
    for model in models or list(MODELS):
        for run in current_runs(settings, model, cases):
            if plain and run.get("variant"):
                continue
            run["events"] = [slim(event) for event in run["events"]]
            del run["usage"]
            runs.append(run)
    ran = {run["case"] for run in runs}
    return {
        "cases": [case_card(settings, c) for c in cases if c.id in ran],
        "conditions": {c.id: {"label": c.label, "summary": c.summary} for c in CONDITIONS.values()},
        "runs": runs,
    }


def write_page(
    settings: Settings,
    out: Path,
    models: list[str] | None,
    only: list[str] | None,
    plain: bool = False,
) -> Path:
    """Write the viewer page. With `plain`, runs made under a run setting are left out."""
    data = json.dumps(
        page_data(settings, models, only, plain), ensure_ascii=False, separators=(",", ":")
    )
    # Binary tool output decodes to replacement characters, which a page may not contain.
    data = data.replace("\ufffd", "?")
    # A closing tag inside the data would end the page's script early.
    page = TEMPLATE.read_text().replace(MARK, data.replace("</", "<\\/"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out
