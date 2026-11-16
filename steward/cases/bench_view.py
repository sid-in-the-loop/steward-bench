"""Write the benchmark viewer: every stored session by domain, level, case and model, with
its transcript laid out loop by loop."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from steward.cases.bench import BENCH_MODELS, LEVELS, bench_cases, plan, problems
from steward.cases.case import DOMAIN_NAMES, Case
from steward.cases.commands import MODELS
from steward.cases.levels import EXCHANGE_CHANCE, LEVEL_NAMES
from steward.cases.session_commands import sessions_path, stored_key, stored_sessions
from steward.cases.sessions import SessionRun, SessionSpec, Setting
from steward.config import Settings

TEMPLATE = Path(__file__).with_name("bench_view.html")
# How much of a tool's output and of a reasoning summary the viewer carries. The stored
# session keeps all of it.
OUTPUT_LIMIT = 3000
THOUGHT_LIMIT = 1500
# Above this many bytes of transcripts the data is written beside the page and loaded when a
# domain and level are chosen; below it the page carries everything.
INLINE_LIMIT = 6_000_000
PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter"}
NOTES = {
    "out_of_date": "out of date",
    "accurate": "accurate",
    "missing": "none",
}


def listed(numbers: tuple[int, ...]) -> str:
    if not numbers:
        return "none"
    head = ", ".join(str(n) for n in numbers[:-1])
    return f"{head} and {numbers[-1]}" if head else str(numbers[-1])


def user_config(setting: Setting, names: dict[str, str]) -> list[dict[str, str]]:
    """Each user of a setting and how they behave: drawn one by one from Level III, and from
    the shared speaking rate below it."""
    users = [
        {
            "role": "requester",
            "name": names.get("requester", "requester"),
            "speaks": "gives each loop's task",
            "unrelated": "",
            "stance": "believes the notes",
        }
    ]
    if not setting.voices:
        rate = f"unprompted, {setting.speaking} rate"
        for role in setting.present[1:]:
            name = names.get(role, role)
            users.append(
                {"role": role, "name": name, "speaks": rate, "unrelated": "", "stance": ""}
            )
        return users
    for voice in setting.voices:
        stance = ""
        if voice.role == "owner":
            stance = "says what changed" if voice.warns else "does not say what changed"
        elif voice.role == "bystander":
            stance = "believes nothing changed" if voice.knowledge == "belief" else "knows nothing"
        elif voice.role == "manager":
            stance = "pushes for speed" if voice.pushes else "does not push"
        speaks = f"{round(voice.rate * 100)}% of handovers" if voice.rate else "only when asked"
        users.append(
            {
                "role": voice.role,
                "name": names.get(voice.role, voice.role),
                "speaks": speaks,
                "unrelated": f"{round(voice.noise * 100)}%" if voice.rate and voice.noise else "",
                "stance": stance,
            }
        )
    return users


def config_of(setting: Setting, names: dict[str, str]) -> dict[str, Any]:
    """A setting laid out for the viewer: short tags for the collapsed line, every field as a
    row, and its users as a table."""
    drift = len(setting.changed)
    tags = [f"{setting.items} loops", f"{drift} drift" if drift else "no drift"]
    tags.append(f"{len(setting.present)} users")
    rows = [
        ("Loops", str(setting.items)),
        ("Drift loops", listed(setting.changed)),
        ("Handover notes", NOTES[setting.notes]),
    ]
    if setting.second:
        rows.append(("Second change", f"{setting.second} object, before each drift loop"))
        tags.append(f"second: {setting.second}")
    if setting.carried:
        rows.append(("Change carried", f"{setting.carried} more loops"))
        tags.append(f"carried {setting.carried}")
    if setting.generated is not None:
        rows.append(("Owner's change", f"recording {setting.generated:02d}"))
        tags.append(f"recording {setting.generated:02d}")
    if setting.level == "III":
        rows.append(("Chat activity", f"{setting.activity} of {len(EXCHANGE_CHANCE) - 1}"))
        tags.append(f"activity {setting.activity}")
    if setting.seed is not None:
        rows.append(("Seed", str(setting.seed)))
    rows.append(("Setting", setting.id))
    return {
        "tags": tags,
        "rows": [list(row) for row in rows],
        "users": user_config(setting, names),
    }


def clipped(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"\n[{len(text) - limit} more characters in the stored session]"


def loop_records(run: SessionRun) -> list[dict[str, Any]]:
    by_item: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for event in run.events:
        by_item[event.item].append(
            {
                "who": event.who,
                "name": event.name,
                "kind": event.kind,
                "text": clipped(event.text, OUTPUT_LIMIT if event.kind == "result" else 20_000),
                "tool": event.tool,
                "thought": clipped(event.thought, THOUGHT_LIMIT),
                "simulated": event.simulated,
                "content": event.content,
            }
        )
    setting = run.setting
    loops = []
    for item in run.items:
        loops.append(
            {
                "number": item.item,
                "drift": item.changed,
                "carried": item.carried,
                "second": setting.second if item.item in setting.changed else None,
                "ending": item.ending,
                "commands": item.commands,
                "looked_first": item.looked_first,
                "asked": item.asked,
                "asked_first": item.asked_first,
                "harm": item.harm,
                "second_harm": item.second_harm,
                "beyond_authority": item.beyond_authority,
                "disclosed": item.disclosed,
                "done": item.done,
                "done_later": item.done_later,
                "heard": item.heard,
                "checks": item.check_output,
                "events": by_item.get(item.item, []),
            }
        )
    return loops


def people_of(case: Case, spec: SessionSpec) -> list[dict[str, str]]:
    people = [*case.people, spec.manager]
    return [{"role": p.role, "name": p.name, "description": p.description} for p in people]


def session_record(run: SessionRun, case: Case, spec: SessionSpec) -> dict[str, Any]:
    """Everything the viewer shows for one session, with its transcript by loop."""
    names = {person["role"]: person["name"] for person in people_of(case, spec)}
    drift = [item for item in run.items if item.changed]
    model = MODELS.get(run.model)
    return {
        "id": "|".join(str(part) for part in stored_key(run)) + f"|{run.model}",
        "case": run.case,
        "title": case.title,
        "domain": case.domain,
        "level": run.setting.level,
        "setting": run.setting.id,
        "config": config_of(run.setting, names),
        "model": run.model,
        "provider": PROVIDERS.get(model.dialect, "Other") if model else "Other",
        "trial": run.trial,
        "simulator": run.simulator,
        "cost_usd": round(run.cost_usd, 4),
        "requests": run.usage.requests,
        "loops": len(run.items),
        "drift_loops": len(drift),
        "harmed": sum(bool(item.harm) for item in drift),
        "asked": sum(bool(item.asked) for item in drift),
        "reported": sum(item.ending == "reported" for item in run.items),
        "problems": problems(run),
        "situation": case.situation,
        "harmful_action": case.harmful_action,
        "safe_path": case.safe_path,
        "harm_statement": case.harm_statement,
        "task": spec.task,
        "people": people_of(case, spec),
        "transcript": loop_records(run),
    }


def collect(
    settings: Settings,
    models: list[str] | None = None,
    only: list[str] | None = None,
    levels: list[str] | None = None,
) -> list[dict[str, Any]]:
    """The newest stored session for every planned case, setting and repeat."""
    cases = bench_cases(settings, only)
    jobs, _ = plan(settings, cases, list(LEVELS))
    wanted = {job.key: job for job in jobs}
    records = []
    for name in models or list(BENCH_MODELS):
        newest: dict[Any, SessionRun] = {}
        for run in stored_sessions(sessions_path(settings, name)):
            if stored_key(run) in wanted and (not levels or run.setting.level in levels):
                newest[stored_key(run)] = run
        for key, run in newest.items():
            job = wanted[key]
            records.append(session_record(run, job.case, job.spec))
    order = {level: index for index, level in enumerate(LEVELS)}
    records.sort(
        key=lambda r: (r["domain"], order[r["level"]], r["title"], r["setting"], r["model"])
    )
    return records


def write_view(records: list[dict[str, Any]], out: Path, title: str) -> tuple[Path, int]:
    """Write the page, and beside it the transcripts by domain, level and model when they are
    too large for the page to carry. Returns the page and how many data files were written."""
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    index = []
    for record in records:
        key = f"{record['domain']}-{record['level']}-{record['model']}"
        groups[key].append(record)
        index.append({k: v for k, v in record.items() if k != "transcript"} | {"file": key})
    files = {key: json.dumps(group, separators=(",", ":")) for key, group in groups.items()}
    inline = sum(len(text) for text in files.values()) <= INLINE_LIMIT
    data = {
        "title": title,
        "domains": DOMAIN_NAMES,
        "levels": LEVEL_NAMES,
        "sessions": index,
        "inline": {key: json.loads(text) for key, text in files.items()} if inline else None,
    }
    written = 0
    if not inline:
        (out / "data").mkdir(exist_ok=True)
        for key, text in files.items():
            (out / "data" / f"{key}.json").write_text(text)
            written += 1
    embedded = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text().replace("__TITLE__", title).replace("__DATA__", embedded)
    path = out / "index.html"
    path.write_text(page)
    return path, written
