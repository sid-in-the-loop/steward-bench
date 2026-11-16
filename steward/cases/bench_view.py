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
from steward.cases.levels import LEVEL_NAMES
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
    "out_of_date": "handover notes out of date",
    "accurate": "handover notes accurate",
    "missing": "no handover notes",
}


def loops_in_words(numbers: tuple[int, ...]) -> str:
    if not numbers:
        return "no drift loop"
    listed = ", ".join(str(n) for n in numbers[:-1])
    joined = f"{listed} and {numbers[-1]}" if listed else str(numbers[-1])
    return f"drift on loop{'s' if len(numbers) > 1 else ''} {joined}"


def voice_in_words(setting: Setting, names: dict[str, str]) -> list[str]:
    said = []
    for voice in setting.voices:
        name = names.get(voice.role, voice.role)
        if voice.rate == 0:
            said.append(f"{name} ({voice.role}) answers when asked")
            continue
        traits = [f"speaks at {round(voice.rate * 100)}% of handovers"]
        if voice.noise:
            traits.append(f"{round(voice.noise * 100)}% of it unrelated")
        if voice.role == "owner":
            traits.append("says what changed" if voice.warns else "does not say what changed")
        if voice.role == "bystander":
            believes = voice.knowledge == "belief"
            traits.append("believes nothing changed" if believes else "knows nothing")
        if voice.role == "manager":
            traits.append("pushes for speed" if voice.pushes else "does not push")
        said.append(f"{name} ({voice.role}) {', '.join(traits)}")
    return said


def setting_in_words(setting: Setting, names: dict[str, str]) -> str:
    """A setting as one sentence a reader can follow without the code."""
    parts = [f"{setting.items} loops", loops_in_words(setting.changed)]
    if setting.voices:
        parts += voice_in_words(setting, names)
    else:
        present = ", ".join(f"{names.get(r, r)} ({r})" for r in setting.present)
        parts.append(f"users: {present}")
        if setting.users > 1:
            parts.append(f"users speak unprompted at the {setting.speaking} rate")
    parts.append(NOTES[setting.notes])
    if setting.second:
        parts.append(f"a second person changes the {setting.second} object before each drift loop")
    if setting.carried:
        parts.append(f"the change stays in place for {setting.carried} more loops")
    if setting.generated is not None:
        parts.append(f"the change is recording {setting.generated} of a simulated owner")
    return "; ".join(parts) + "."


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
        "setting_words": setting_in_words(run.setting, names),
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
