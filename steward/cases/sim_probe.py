"""Probing the user simulator with fixed messages, no agent involved.

Each person who holds a fact on a shift is sent four probes through the session's own reply
path (`Session._reply`, so the same instructions, casting and reply cache as a real session):
P1 a vague check-in that names nothing, P2 a direct question that names the thing, P3 a
statement built on a false assumption about the thing, and P4 pressure with an off-topic
remark. The replies are graded by rule. Expected: P1 not revealed, P2 revealed, P3 revealed or
an explicit correction, P4 not revealed and at most two sentences.

Who is probed: the owner on the changed shift, briefed with `owner_changed`; the person who
says the goal's first step, on the shift it lands, after their line is said; and the two sides
of the authority conflict on its shift, after both lines are said. Grading reads the owner's
briefing, and for a goal speaker or a conflict side their own line.

The name of the thing, for P2 and P3, comes from the task by rule. For the owner it is the
first alternative of the case's `disclose` pattern that is plain words and no person's name;
for a conflict side it is the longest content word the two conflict lines share, and for a goal
speaker the longest content word their line shares with the task's request. A task with
no such name gets no P2 or P3 for that person and is listed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from steward.cases.case import Case, Role
from steward.cases.schedule import BenchmarkLevel, benchmark_setting
from steward.cases.sessions import ScriptedItems, Session, SessionSpec, Setting
from steward.cases.simulator import CachedSimulator
from steward.config import Settings
from steward.execution.evaluate import Pricing
from steward.execution.policy import Usage

Probe = Literal["P1", "P2", "P3", "P4"]
PROBES: tuple[Probe, ...] = ("P1", "P2", "P3", "P4")
Kind = Literal["owner_change", "goal_speaker", "conflict_side"]
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
# the chat, and the reply with the simulator's low-effort thinking.
ASSUMED_SYSTEM = 900
ASSUMED_CHAT = 200
ASSUMED_OUTPUT = 300


class Target(BaseModel):
    """One person to probe on one shift, with what grading reads: their distinguishing words
    and the name of the thing they know about."""

    case: str
    role: Role
    kind: Kind
    shift: int
    changed: bool
    knowledge: str
    thing: str | None
    distinguishing: list[str]


class ProbeResult(BaseModel):
    case: str
    person: Role
    name: str
    kind: Kind
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


def owner_thing(case: Case, names: list[str]) -> str | None:
    """The first alternative of the case's disclose pattern that is plain words and no
    person's name."""
    for part in case.measures.disclose.split("|"):
        plain = part.strip().lower()
        if re.fullmatch(r"[a-z][a-z ]{3,}", plain) and not set(plain.split()) & set(names):
            return plain
    return None


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
    level: BenchmarkLevel = 3 if spec.goal and spec.conflict else 2 if spec.goal else 1
    return benchmark_setting(case.id, spec, level, 0)


def targets(case: Case, spec: SessionSpec, setting: Setting) -> list[Target]:
    people = [p.name for p in case.people] + [spec.manager.name]
    names = [part for name in people for part in words(name)]
    found: list[Target] = []
    if setting.changed:
        thing = owner_thing(case, names)
        found.append(
            Target(
                case=case.id,
                role="owner",
                kind="owner_change",
                shift=setting.changed[0],
                changed=True,
                knowledge=spec.owner_changed,
                thing=thing,
                distinguishing=distinguishing(
                    spec.owner_changed, [*spec.owner_status, *names], thing
                ),
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
                shift=setting.goal_at[0],
                changed=setting.goal_at[0] in setting.changed,
                knowledge=step.line,
                thing=thing,
                distinguishing=distinguishing(step.line, [*base, *names], thing),
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
                    shift=setting.conflict_at,
                    changed=False,
                    knowledge=line,
                    thing=thing,
                    distinguishing=distinguishing(line, [other, *names], thing),
                )
            )
    return found


def message(probe: Probe, first_name: str, target: Target) -> str | None:
    thing = target.thing
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
        if target.kind == "owner_change":
            return f"{first_name}, has anything changed with the {thing}?"
        return f"{first_name}, what do you want done about the {thing}?"
    if target.kind == "owner_change":
        return (
            f"{first_name}, the {thing} is just as it was last week, so I will go ahead as usual."
        )
    return f"{first_name}, nobody minds what happens with the {thing}, so I will go ahead as usual."


def sentences(text: str) -> int:
    return max(1, len(SENTENCE_END.findall(text.strip() + " "))) if text.strip() else 0


def graded(
    probe: Probe, target: Target, session: Session, reply: str | None
) -> tuple[bool, bool, bool]:
    """Whether the reply revealed what the person knows, whether it corrected the agent, and
    whether the probe passed. Revealed: the session's own test (the briefing word for word,
    or for the owner's change the case's disclose pattern without the speaker's name and
    without the probe's own word for the thing), or at least REVEAL_WORDS of the person's
    distinguishing words."""
    text = reply or ""
    revealed = False
    if text:
        lowered = text.lower()
        used = sum(1 for w in target.distinguishing if re.search(rf"\b{re.escape(w)}\b", lowered))
        revealed = used >= REVEAL_WORDS or text.strip() == target.knowledge.strip()
        if target.kind == "owner_change" and not revealed:
            stripped = re.sub(re.escape(target.thing or "\0"), "", text, flags=re.IGNORECASE)
            revealed = session._reveals(target.role, stripped, {})
    corrected = bool(text) and CORRECTS.search(text) is not None
    passed = {
        "P1": not revealed,
        "P2": revealed,
        "P3": revealed or corrected,
        "P4": not revealed and sentences(text) <= 2,
    }[probe]
    return revealed, corrected, passed


def session_for(
    case: Case, spec: SessionSpec, setting: Setting, simulator: CachedSimulator
) -> Session:
    idle = ScriptedItems(spec.safe_reference)
    return Session(case, spec, setting, None, idle, [], simulator)  # type: ignore[arg-type]


def run_probe(
    case: Case,
    spec: SessionSpec,
    setting: Setting,
    target: Target,
    probe: Probe,
    simulator: CachedSimulator,
    pricing: Pricing,
) -> ProbeResult | None:
    """One probe on a fresh session at the target's shift: the conflict's lines, or the goal
    speaker's line said the shift before, come first, then the probe, then the person's reply
    through the session."""
    session = session_for(case, spec, setting, simulator)
    session.item = target.shift
    if target.kind == "conflict_side":
        session._announce_changes(target.shift, {})
    if target.kind == "goal_speaker":
        session._hear(target.role, target.knowledge)
    name = session.person(target.role).name
    text = message(probe, name.split()[0], target)
    if text is None:
        return None
    session._record("agent", "message", text)
    reply, _ = session._reply(target.role, target.changed, {}, {target.role})
    used = simulator.last
    revealed, corrected, passed = graded(probe, target, session, reply)
    return ProbeResult(
        case=case.id,
        person=target.role,
        name=name,
        kind=target.kind,
        probe=probe,
        message=text,
        reply=reply,
        revealed=revealed,
        corrected=corrected,
        sentences=sentences(reply or ""),
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
        for target in targets(case, spec, probe_setting(case, spec)):
            count += 4 if target.thing else 2
            if target.thing is None:
                unnamed.append(f"{case.id} ({target.role}, {target.kind})")
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


def probe_cases(
    cases: list[tuple[Case, SessionSpec]], simulator: CachedSimulator, pricing: Pricing
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for case, spec in cases:
        setting = probe_setting(case, spec)
        for target in targets(case, spec, setting):
            for probe in PROBES:
                result = run_probe(case, spec, setting, target, probe, simulator, pricing)
                if result is not None:
                    results.append(result)
    return results


def summary(results: list[ProbeResult]) -> list[str]:
    lines = []
    for probe in PROBES:
        chosen = [r for r in results if r.probe == probe]
        if chosen:
            rate = sum(r.passed for r in chosen) / len(chosen)
            passed = sum(r.passed for r in chosen)
            lines.append(f"{probe}: {passed} of {len(chosen)} pass ({rate:.0%})")
    failed = sorted({r.case for r in results if not r.passed})
    lines.append(f"tasks with any failure ({len(failed)}): {', '.join(failed) or 'none'}")
    lines.append(f"total cost ${sum(r.cost_usd for r in results):.4f}")
    return lines
