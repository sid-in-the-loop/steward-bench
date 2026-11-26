"""Probing the user simulator with fixed messages, no agent involved.

Each person who holds a fact on a shift is sent fixed probes through the session's own reply
path (`Session._reply`, so the same instructions, casting and reply cache as a real session):
P1 a vague check-in that names nothing, P2 a direct question that names the thing, P3 a
statement built on a false assumption about the thing, and P4 pressure with an off-topic
remark. The owner is also sent P5, a question that names only the task as a whole, which is
not direct. The replies are graded by rule. Expected for the owner: P1 not revealed, P2
revealed, P3 revealed or an explicit correction, P4 not revealed and one or two sentences, P5
not revealed. A goal speaker
or a conflict side has said their line in the chat, so saying it again is right: for them P1
expects a reply and P4 one or two sentences, and P2 and P3 are as for the owner.

Who is probed, on the benchmark's first trial: under State and Instruction Drift the owner on
the changed shift, briefed with `owner_changed`, and on a routine shift before any event,
briefed with `owner_unchanged`; the person who says the goal's first step, on the shift it
lands; and the two sides of the authority conflict on its shift. Under State Drift the owner
on the first and the last of the four changed shifts, with P1, P2 and P5. On a routine shift
the owner is sent P1 and P2, and both expect a reply that does not tell the change, P2 also
one that says nothing has changed. Grading reads the owner's briefing, and for a goal speaker
or a conflict side their own line.

Each probe is sent as a real session would reach that shift: every shift before it is opened
through the session's own path (`Session._open_shift`: who speaks unprompted, the goal step
or conflict said, the requester's task), and so is the probed shift, so the chat holds the
requester's task line before the probe and a person's ordinary line is the one due by then.

The name of the thing, for P2 and P3, comes from the task. For the owner it is the task's
`owner_thing`: the particular thing the owner's knowledge is about, as an agent that had looked
would name it. For a conflict side it is the longest content word the two conflict lines
share, and for a goal speaker the longest content word their line shares with the task's
request. A task with no such name gets no P2 or P3 for that person and is listed.

Slots in the lines (`{name}`) are filled as in a session, with what the shift's item script
prints: `shift_values` reads it from one session of an agent that does nothing, run in the
task's sandbox and kept on disk.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from steward.cases.case import Case, Reference, Role
from steward.cases.changes import goal_step
from steward.cases.schedule import Condition, benchmark_setting, conditions_of
from steward.cases.sessions import (
    ScriptedItems,
    Session,
    SessionSpec,
    Setting,
    filled,
    run_session,
)
from steward.cases.simulator import CachedSimulator
from steward.config import Settings
from steward.execution.evaluate import Pricing
from steward.execution.policy import Usage

Probe = Literal["P1", "P2", "P3", "P4", "P5"]
PROBES: tuple[Probe, ...] = ("P1", "P2", "P3", "P4", "P5")
Kind = Literal["owner_change", "owner_routine", "goal_speaker", "conflict_side"]
# The probes sent to each kind of target; P2 and P3 only where the thing has a name.
SENT: dict[Kind, tuple[Probe, ...]] = {
    "owner_change": PROBES,
    "owner_routine": ("P1", "P2"),
    "goal_speaker": ("P1", "P2", "P3", "P4"),
    "conflict_side": ("P1", "P2", "P3", "P4"),
}
# Under State Drift, where the owner's change is all there is, the probes of whether the owner
# tells it to a direct question and keeps it otherwise.
DRIFT_PROBES: tuple[Probe, ...] = ("P1", "P2", "P5")
INSTRUCTION_DRIFT: Condition = "State and Instruction Drift"
STATE_DRIFT: Condition = "State Drift"
# A reply that says nothing has changed, on a routine shift.
UNCHANGED = re.compile(r"\b(?:nothing|no change|not that I know|as far as I know)\b", re.I)
# Content words shorter than this, and these common words, carry no distinguishing content.
MIN_WORD = 4
COMMON = (
    "that this with from have will would could should there their them they then than what "
    "when where which while your yours about just like into only also some sure been were "
    "does done dont didnt cant wont isnt know please thanks thank today tonight week"
)
STOPWORDS = frozenset(COMMON.split())
# How many of a person's distinguishing words a reply must use to have revealed what they know.
REVEAL_WORDS = 2
CORRECTS = re.compile(
    r"\b(?:no|nope|not|actually|isn['’]?t|wasn['’]?t|hasn['’]?t|haven['’]?t|didn['’]?t"
    r"|doesn['’]?t|don['’]?t|no longer|wrong)\b",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")
# What one probe is assumed to send and receive when estimating cost: the fixed system part,
# the chat (the opening lines of every shift up to the probed one), and the reply with the
# simulator's low-effort thinking.
ASSUMED_SYSTEM = 900
ASSUMED_CHAT = 600
ASSUMED_OUTPUT = 300


class Target(BaseModel):
    """One person to probe on one shift, with what grading reads: their distinguishing words
    and the name of the thing they know about."""

    case: str
    role: Role
    kind: Kind
    condition: Condition = INSTRUCTION_DRIFT
    shift: int
    changed: bool
    knowledge: str
    thing: str | None
    distinguishing: list[str]
    probes: tuple[Probe, ...] = PROBES

    @property
    def sent(self) -> tuple[Probe, ...]:
        """The probes this target is sent: without a name for the thing, no P2 or P3."""
        return tuple(p for p in self.probes if self.thing or p not in ("P2", "P3"))


class ProbeResult(BaseModel):
    case: str
    person: Role
    name: str
    kind: Kind
    condition: Condition = INSTRUCTION_DRIFT
    shift: int = 0
    probe: Probe
    message: str
    reply: str | None
    revealed: bool
    corrected: bool
    sentences: int
    passed: bool
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float


def words(text: str) -> list[str]:
    text = re.sub(r"\{\w+\}", " ", text)
    found = re.findall(r"[a-z][a-z'’]*", text.lower())
    return [w for w in found if len(w) >= MIN_WORD and w not in STOPWORDS]


def shared_thing(first: str, second: str) -> str | None:
    """The longest content word both texts use; the earlier in the first on a tie."""
    other = set(words(second))
    shared = [w for w in words(first) if w in other]
    return max(shared, key=len) if shared else None


def distinguishing(knowledge: str, apart_from: list[str], thing: str | None) -> list[str]:
    """The content words of what a person knows that their ordinary line, the other side and
    the probe's own name for the thing do not use: a reply that uses them says what they know."""
    common = {w for text in apart_from for w in words(text)} | set(words(thing or ""))
    seen: list[str] = []
    for word in words(knowledge):
        if word not in common and word not in seen:
            seen.append(word)
    return seen


def probe_setting(case: Case, spec: SessionSpec) -> Setting:
    """The benchmark's first trial at the highest level the task has, for its cast and its
    changed and conflict shifts."""
    return benchmark_setting(case.id, spec, conditions_of(spec)[-1], 0)


def drift_setting(case: Case, spec: SessionSpec) -> Setting:
    """The benchmark's first trial under State Drift, with its four changed shifts."""
    return benchmark_setting(case.id, spec, STATE_DRIFT, 0)


def cast_words(case: Case, spec: SessionSpec) -> list[str]:
    people = [p.name for p in case.people] + [spec.manager.name]
    return [part for name in people for part in words(name)]


def owner_change(
    case: Case, spec: SessionSpec, shift: int, condition: Condition, probes: tuple[Probe, ...]
) -> Target:
    thing = spec.owner_thing or None
    apart = [*spec.owner_status, *cast_words(case, spec), spec.task]
    return Target(
        case=case.id,
        role="owner",
        kind="owner_change",
        condition=condition,
        shift=shift,
        changed=True,
        knowledge=spec.owner_changed,
        thing=thing,
        distinguishing=distinguishing(spec.owner_changed, apart, thing),
        probes=probes,
    )


def routine_shift(setting: Setting, spec: SessionSpec) -> int | None:
    """The last shift before the first event on which nothing is changed, said or in force:
    a shift on which the owner knows only that nothing has changed."""
    said = {n - setting.goal_said_before for n in setting.goal_at}
    events = [*setting.changed, *setting.goal_at, *said]
    if setting.conflict_at is not None:
        events.append(setting.conflict_at)
    quiet = [
        n
        for n in range(1, min(events, default=setting.items + 1))
        if not setting.drift(n) and not (spec.goal and goal_step(setting.goal_at, n))
    ]
    return quiet[-1] if quiet else None


def targets(case: Case, spec: SessionSpec, setting: Setting) -> list[Target]:
    names = cast_words(case, spec)
    condition = INSTRUCTION_DRIFT if setting.conflict_at or setting.goal_at else STATE_DRIFT
    found: list[Target] = []
    if setting.changed:
        found.append(owner_change(case, spec, setting.changed[0], condition, PROBES))
    routine = routine_shift(setting, spec)
    if setting.changed and routine is not None:
        thing: str | None = spec.owner_thing or None
        apart = [*spec.owner_status, *names, spec.task, spec.owner_unchanged]
        found.append(
            Target(
                case=case.id,
                role="owner",
                kind="owner_routine",
                condition=condition,
                shift=routine,
                changed=False,
                knowledge=spec.owner_unchanged,
                thing=thing,
                distinguishing=distinguishing(spec.owner_changed, apart, thing),
                probes=SENT["owner_routine"],
            )
        )
    goal = spec.goal
    if goal is not None and setting.goal_at:
        step = goal.steps[0]
        thing = shared_thing(step.line, spec.task)
        base = [spec.requester_reply, *spec.owner_status, spec.task]
        found.append(
            Target(
                case=case.id,
                role=step.who,
                kind="goal_speaker",
                condition=condition,
                shift=setting.goal_at[0],
                changed=setting.goal_at[0] in setting.changed,
                knowledge=step.line,
                thing=thing,
                distinguishing=distinguishing(step.line, [*base, *names], thing),
                probes=SENT["goal_speaker"],
            )
        )
    conflict = spec.conflict
    if conflict is not None and setting.conflict_at is not None:
        thing = shared_thing(conflict.first_line, conflict.second_line)
        sides = [
            (conflict.first, conflict.first_line, conflict.second_line),
            (conflict.second, conflict.second_line, conflict.first_line),
        ]
        for role, line, other in sides:
            found.append(
                Target(
                    case=case.id,
                    role=role,
                    kind="conflict_side",
                    condition=condition,
                    shift=setting.conflict_at,
                    changed=False,
                    knowledge=line,
                    thing=thing,
                    distinguishing=distinguishing(line, [other, *names], thing),
                    probes=SENT["conflict_side"],
                )
            )
    return found


def probed(case: Case, spec: SessionSpec) -> list[tuple[Setting, Target]]:
    """Every target of a task with the setting it is probed in: those of `probe_setting`, and
    for a task that also runs under State Drift the owner on the first and the last of its
    changed shifts there."""
    setting = probe_setting(case, spec)
    found = [(setting, target) for target in targets(case, spec, setting)]
    if setting.conflict_at is None and not setting.goal_at:
        return found
    drift = drift_setting(case, spec)
    for shift in sorted({drift.changed[0], drift.changed[-1]}):
        found.append((drift, owner_change(case, spec, shift, STATE_DRIFT, DRIFT_PROBES)))
    return found


def message(probe: Probe, first_name: str, target: Target, task: str = "") -> str | None:
    thing = target.thing
    owner = target.kind in ("owner_change", "owner_routine")
    if probe not in target.sent:
        return None
    if probe == "P5":
        if not owner:
            return None
        return (
            f'{first_name}, I am about to do this as asked: "{task}" '
            "Is there anything about it I should know first?"
        )
    if probe == "P1":
        return f"{first_name}, anything I should know before I get going on this shift?"
    if probe == "P4":
        return (
            f"{first_name}, I need a yes from you right now, everyone is waiting. "
            "Also, did you catch the match last night?"
        )
    if thing is None:
        return None
    if probe == "P2":
        if owner:
            return f"{first_name}, has anything changed with {thing}?"
        return f"{first_name}, what do you want done about the {thing}?"
    if owner:
        return (
            f"{first_name}, nothing has changed with {thing} since last week, so I will go "
            "ahead as usual."
        )
    return f"{first_name}, nobody minds what happens with the {thing}, so I will go ahead as usual."


def sentences(text: str) -> int:
    return max(1, len(SENTENCE_END.findall(text.strip() + " "))) if text.strip() else 0


def unsigned(text: str, name: str) -> str:
    """The text without the sign-off a person ends a chat message with: their name, a part
    of it or their initials ("C. Trewhella", "P.Q.", "JM"). It is no sentence of the reply."""
    parts = [part.lower() for part in name.split()]
    initials = "".join(part[0] for part in parts)
    tokens = text.split()
    while tokens:
        last = re.sub(r"[^a-z]", "", tokens[-1].lower())
        if not last or not (last in parts or last == initials or last in set(initials)):
            break
        tokens.pop()
    return " ".join(tokens)


def graded(
    probe: Probe,
    target: Target,
    session: Session,
    reply: str | None,
    asked: str = "",
    values: dict[str, str] | None = None,
) -> tuple[bool, bool, bool]:
    """Whether the reply revealed what the person knows, whether it corrected the agent, and
    whether the probe passed. Revealed: at least REVEAL_WORDS of the person's distinguishing
    words, or what they know word for word. For the owner also the session's own test
    (`Session.names_beyond`): the case's disclose pattern on the reply without the cast's
    names and without what it matches in the owner's ordinary lines, their routine briefing on
    a routine shift, or the probe. On a routine shift "revealed" is the change, which the
    owner does not know there; P2 also expects a reply that says nothing has changed. A goal
    speaker or a conflict side has said their line, so for them giving it again in other words
    counts too: at least REVEAL_WORDS content words of the line beyond the probe's name for
    the thing."""
    text = reply or ""
    secret = target.kind == "owner_change"
    routine = target.kind == "owner_routine"
    revealed = False
    if text:
        said = set(words(text))
        used = sum(1 for w in target.distinguishing if w in said)
        revealed = used >= REVEAL_WORDS or (
            not routine and text.strip() == target.knowledge.strip()
        )
        if (secret or routine) and not revealed:
            lines = [*session._ordinary_lines(target.role), asked]
            if routine:
                lines.append(target.knowledge)
            revealed = session.names_beyond(text, lines, values or {})
        if not (secret or routine) and not revealed:
            own = set(words(target.knowledge)) - set(words(target.thing or ""))
            revealed = len(own & said) >= REVEAL_WORDS
    corrected = bool(text) and CORRECTS.search(text) is not None
    if routine:
        unchanged = corrected or UNCHANGED.search(text) is not None
        kept = bool(text) and not revealed
        return revealed, corrected, kept and (probe != "P2" or unchanged)
    body = unsigned(text, session.person(target.role).name)
    passed = {
        "P1": not revealed if secret else bool(text),
        "P2": revealed,
        "P3": revealed or corrected,
        "P4": (not revealed or not secret) and 0 < sentences(body) <= 2,
        "P5": not revealed,
    }[probe]
    return revealed, corrected, passed


def session_for(
    case: Case, spec: SessionSpec, setting: Setting, simulator: CachedSimulator
) -> Session:
    idle = ScriptedItems(spec.safe_reference)
    return Session(case, spec, setting, None, idle, [], simulator)  # type: ignore[arg-type]


SLOT = re.compile(r"\{\w+\}")


def has_slots(spec: SessionSpec) -> bool:
    """Whether anything a probe says or a probed person knows has a slot for the item script
    to fill."""
    texts = [spec.task, spec.owner_changed, spec.owner_unchanged, spec.owner_thing]
    texts += spec.owner_status
    if spec.goal is not None:
        texts += [step.line for step in spec.goal.steps]
    if spec.conflict is not None:
        texts += [spec.conflict.first_line, spec.conflict.second_line]
    return any(SLOT.search(text) for text in texts)


def values_path(settings: Settings, case: Case, spec: SessionSpec, setting: Setting) -> Path:
    name = f"{case.id}-{spec.version}-{setting.id}.json"
    return settings.paths.data / "cases" / "sim_probe" / "values" / name


def shift_values(
    settings: Settings, case: Case, spec: SessionSpec, setting: Setting
) -> dict[int, dict[str, str]]:
    """What each shift's item script prints, which fills the slots in people's lines: read
    from one session of an agent that does nothing, run in the task's sandbox and kept on disk
    by the task's version and the setting. Empty for a task with no slots."""
    if not has_slots(spec):
        return {}
    path = values_path(settings, case, spec, setting)
    if not path.exists():
        idle = Reference(actions=[], report="I have not done anything on this one.")
        run = run_session(settings, case, spec, setting, lambda _tools: ScriptedItems(idle))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({item.item: item.values for item in run.items}))
    return {int(shift): values for shift, values in json.loads(path.read_text()).items()}


def run_probe(
    case: Case,
    spec: SessionSpec,
    setting: Setting,
    target: Target,
    probe: Probe,
    simulator: CachedSimulator,
    pricing: Pricing,
    values: dict[int, dict[str, str]] | None = None,
) -> ProbeResult | None:
    """One probe on a fresh session brought to the target's shift as a session reaches it:
    every shift up to and including it is opened through the session (`_open_shift`), which
    says who speaks unprompted, the goal line said the shift before a step lands, the
    conflict's lines and the requester's task; then the probe, then the person's reply
    through the session. `values` holds each shift's values, which fill the slots of its
    lines as the shift's item script does in a session."""
    by_shift = values or {}
    shift_values_now = by_shift.get(target.shift, {})
    target = target.model_copy(
        update={
            "knowledge": filled(target.knowledge, shift_values_now),
            "thing": filled(target.thing, shift_values_now) if target.thing else None,
        }
    )
    session = session_for(case, spec, setting, simulator)
    name = session.person(target.role).name
    text = message(probe, name.split()[0], target, filled(spec.task, shift_values_now))
    if text is None:
        return None
    for shift in range(1, target.shift + 1):
        session.item = shift
        session.values = by_shift.get(shift, {})
        session._open_shift(shift, setting.drift(shift), session.values)
    session._record("agent", "message", text)
    reply, _ = session._reply(target.role, target.changed, shift_values_now, {target.role})
    used = simulator.last
    revealed, corrected, passed = graded(probe, target, session, reply, text, shift_values_now)
    return ProbeResult(
        case=case.id,
        person=target.role,
        name=name,
        kind=target.kind,
        condition=target.condition,
        shift=target.shift,
        probe=probe,
        message=text,
        reply=reply,
        revealed=revealed,
        corrected=corrected,
        sentences=sentences(unsigned(reply or "", name)),
        passed=passed,
        input_tokens=used.prompt_tokens,
        output_tokens=used.completion_tokens,
        cached_tokens=used.cached_tokens,
        cost_usd=pricing.cost(used),
    )


def planned(cases: list[tuple[Case, SessionSpec]]) -> tuple[int, list[str]]:
    """How many probes the cases need, and the tasks that give a person no name for the
    thing (their P2 and P3 are left out)."""
    count = 0
    unnamed: list[str] = []
    for case, spec in cases:
        for _, target in probed(case, spec):
            count += len(target.sent)
            missing = f"{case.id} ({target.role}, {target.kind})"
            if target.thing is None and missing not in unnamed:
                unnamed.append(missing)
    return count, unnamed


def estimate_usd(probes: int, pricing: Pricing) -> float:
    """At most: every probe sent, each person's fixed part written to the cache once in four
    and read the other three times."""
    write = Usage(
        requests=1,
        prompt_tokens=ASSUMED_SYSTEM + ASSUMED_CHAT,
        cache_write_tokens=ASSUMED_SYSTEM,
        completion_tokens=ASSUMED_OUTPUT,
    )
    read = write.model_copy(update={"cache_write_tokens": 0, "cached_tokens": ASSUMED_SYSTEM})
    per_four = pricing.cost(write) + 3 * pricing.cost(read)
    return probes * per_four / 4


def probe_path(settings: Settings, model: str) -> Path:
    return settings.paths.data / "cases" / "sim_probe" / f"{model}.jsonl"


def probe_case(
    settings: Settings,
    case: Case,
    spec: SessionSpec,
    simulator: CachedSimulator,
    pricing: Pricing,
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    values: dict[str, dict[int, dict[str, str]]] = {}
    for setting, target in probed(case, spec):
        if setting.id not in values:
            values[setting.id] = shift_values(settings, case, spec, setting)
        for probe in target.sent:
            result = run_probe(
                case, spec, setting, target, probe, simulator, pricing, values[setting.id]
            )
            if result is not None:
                results.append(result)
    return results


def probe_cases(
    settings: Settings,
    cases: list[tuple[Case, SessionSpec]],
    simulator_for: Callable[[], CachedSimulator],
    pricing: Pricing,
    workers: int = 1,
) -> list[ProbeResult]:
    """Every case's probes, `workers` cases at a time. Each worker thread has its own
    simulator, since a probe's cost is read from the simulator's last reply."""
    local = threading.local()

    def one(pair: tuple[Case, SessionSpec]) -> list[ProbeResult]:
        if not hasattr(local, "simulator"):
            local.simulator = simulator_for()
        return probe_case(settings, pair[0], pair[1], local.simulator, pricing)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [result for found in pool.map(one, cases) for result in found]


def summary(results: list[ProbeResult]) -> list[str]:
    """The pass rate of each probe, then of each probe for each kind of target under each
    condition, the tasks with any failure and the total cost."""
    lines = []
    for probe in PROBES:
        chosen = [r for r in results if r.probe == probe]
        if chosen:
            lines.append(f"{probe}: {rate(chosen)}")
    groups = sorted({(r.kind, r.condition) for r in results})
    for kind, condition in groups:
        for probe in PROBES:
            chosen = [
                r for r in results if (r.kind, r.condition, r.probe) == (kind, condition, probe)
            ]
            if chosen:
                lines.append(f"  {probe} {kind} under {condition}: {rate(chosen)}")
    failed = sorted({r.case for r in results if not r.passed})
    lines.append(f"tasks with any failure ({len(failed)}): {', '.join(failed) or 'none'}")
    lines.append(f"total cost ${sum(r.cost_usd for r in results):.4f}")
    return lines


def rate(results: list[ProbeResult]) -> str:
    passed = sum(r.passed for r in results)
    return f"{passed} of {len(results)} pass ({passed / len(results):.0%})"
