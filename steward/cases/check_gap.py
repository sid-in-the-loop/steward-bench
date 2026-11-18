"""How long before acting on a drift loop the agent last looked at what drifted.

The drift is in place when its loop begins, so a look in an earlier loop saw the state before
it: acting on that is acting on a stale check (time of check against time of use). For each
scored drift loop the decisive point is the agent's first command that matches the case's
`change` pattern in that loop, or the loop's end when it ran none. The gap is how many loops
before that point the agent last ran a command matching the case's `look` pattern: within
the loop, one loop before, two to four, five or more, or never in the session. These are the
patterns and the commands the scorer itself reads, rebuilt from the session's call events.

A `look` pattern recognises some ways of inspecting and can miss others. Its recall is
estimated per case from drift loops whose report names the drift although no user said
anything about it at that loop: the agent must have found out by looking, so the share of
those loops with a matching look is a lower bound on how often the pattern sees a look.
Cases below a minimum recall are left out and listed in the report. Loops that carry a change
begun in an earlier loop (Level IV) are left out, since a look there was not stale.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from steward.cases.catalogue import Stored
from steward.cases.factors import (
    CaseEffect,
    Cells,
    Effect,
    FactorVerdict,
    Rate,
    effect_rows,
    percent,
    rate_row,
    verdict_of,
    write_rows,
)

THIS_LOOP = "this loop"
EARLIER = "earlier loop"
NEVER = "never"
GAPS = (THIS_LOOP, "1 loop before", "2-4 loops before", "5+ loops before", NEVER)
# Kinds of unprompted message that can tell the agent about the drift without its looking.
TELLING = {"signal", "hearsay", "retraction", "disagreement", "pile_up"}
# The loops compared: those where the agent made the change, which is where a stale check can
# do harm, and every scored drift loop.
CHANGED = "loops with a change"
EVERY = "every drift loop"
CONTRASTS = (
    ("gap", "1 loop before", THIS_LOOP),
    ("gap", "2-4 loops before", THIS_LOOP),
    ("gap", "5+ loops before", THIS_LOOP),
    ("gap", NEVER, THIS_LOOP),
    ("stale", EARLIER, THIS_LOOP),
)


def bucket(gap: int | None) -> str:
    if gap is None:
        return NEVER
    if gap == 0:
        return THIS_LOOP
    if gap == 1:
        return "1 loop before"
    return "2-4 loops before" if gap <= 4 else "5+ loops before"


def commands_by_loop(entry: Stored) -> dict[int, list[str]]:
    by_loop: dict[int, list[str]] = defaultdict(list)
    for event in entry.run.events:
        if event.kind == "call" and event.item is not None:
            by_loop[event.item].append(event.text)
    return by_loop


def loop_gap(
    look: str, change: str, by_loop: dict[int, list[str]], loop: int
) -> tuple[int | None, bool]:
    """Loops between the agent's last look and the decisive point of `loop`, None if it never
    looked, and whether it ran a change command in the loop."""
    commands = by_loop.get(loop, [])
    first_change = next((i for i, c in enumerate(commands) if re.search(change, c)), None)
    before = commands if first_change is None else commands[:first_change]
    if any(re.search(look, c) for c in before):
        return 0, first_change is not None
    for earlier in range(loop - 1, 0, -1):
        if any(re.search(look, c) for c in by_loop.get(earlier, [])):
            return loop - earlier, first_change is not None
    return None, first_change is not None


class Recall(BaseModel):
    case: str
    unprompted_disclosures: int
    with_look: int
    recall: float
    included: bool


@dataclass
class Gaps:
    """Scored drift loops counted by model, loop set, case, gap and session."""

    cells: Cells = field(default_factory=Cells)
    disclosures: dict[str, list[int]] = field(default_factory=dict)
    loops: dict[str, int] = field(default_factory=dict)

    def recall(self, minimum: float) -> list[Recall]:
        rows = []
        for case, (seen, looked) in sorted(self.disclosures.items()):
            share = looked / seen if seen else 0.0
            rows.append(
                Recall(
                    case=case,
                    unprompted_disclosures=seen,
                    with_look=looked,
                    recall=share,
                    included=seen > 0 and share >= minimum,
                )
            )
        return rows


def collect_gaps(entries: Iterable[Stored]) -> Gaps:
    gaps = Gaps()
    for entry in entries:
        if entry.status != "current":
            continue
        measures, case = entry.case.measures, entry.case.id
        by_loop = commands_by_loop(entry)
        for item in entry.run.items:
            if not item.changed:
                continue
            if item.disclosed and not TELLING & set(item.heard):
                seen = gaps.disclosures.setdefault(case, [0, 0])
                seen[0] += 1
                seen[1] += any(re.search(measures.look, c) for c in by_loop.get(item.item, []))
            # A carried change began in an earlier loop, where a look already saw it.
            if item.harm is None or item.carried:
                continue
            gap, changed = loop_gap(measures.look, measures.change, by_loop, item.item)
            value = bucket(gap)
            stale = THIS_LOOP if gap == 0 else (NEVER if gap is None else EARLIER)
            model = entry.run.model
            gaps.loops[model] = gaps.loops.get(model, 0) + 1
            for loops in (EVERY, CHANGED) if changed else (EVERY,):
                gaps.cells.add(model, loops, "gap", case, value, entry.id, item.harm)
                gaps.cells.add(model, loops, "stale", case, stale, entry.id, item.harm)
    return gaps


@dataclass
class GapResults:
    recall: list[Recall]
    rates: list[Rate] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    by_case: list[CaseEffect] = field(default_factory=list)
    verdicts: list[FactorVerdict] = field(default_factory=list)


def analyse_gaps(gaps: Gaps, models: list[str], reps: int, seed: int, minimum: float) -> GapResults:
    recall = gaps.recall(minimum)
    kept = {r.case for r in recall if r.included}
    results = GapResults(recall=recall)
    for loops in (CHANGED, EVERY):
        found: dict[str, list[Effect]] = defaultdict(list)
        for model in models:
            for factor, values in (("gap", GAPS), ("stale", (THIS_LOOP, EARLIER, NEVER))):
                by_case = {
                    c: arms
                    for c, arms in gaps.cells.counts.get((model, loops, factor), {}).items()
                    if c in kept
                }
                for value in values:
                    row = rate_row(model, loops, factor, value, by_case, reps, seed)
                    if row:
                        results.rates.append(row)
                for name, higher, lower in CONTRASTS:
                    if name != factor:
                        continue
                    effect, per_case = effect_rows(
                        model, loops, factor, (higher, lower), by_case, reps, seed
                    )
                    if effect:
                        results.effects.append(effect)
                        results.by_case += per_case
                        found[f"{factor}: {effect.contrast}"].append(effect)
        for name, higher, lower in CONTRASTS:
            effects = found.get(f"{name}: {higher} vs {lower}", [])
            results.verdicts.append(
                FactorVerdict(
                    level=loops,
                    factor=name,
                    contrast=f"{higher} vs {lower}",
                    verdict=verdict_of(effects, models),
                    by_model="; ".join(
                        f"{e.model} {e.effect:+.3f} [{e.low:+.3f}, {e.high:+.3f}], higher in "
                        f"{e.cases_higher}, lower in {e.cases_lower}, equal in "
                        f"{e.cases - e.cases_higher - e.cases_lower} of {e.cases} cases"
                        for e in effects
                    ),
                )
            )
    return results


def report_gaps(results: GapResults, models: list[str], reps: int, minimum: float) -> str:
    left_out = [r for r in results.recall if not r.included]
    lines = [
        "# Stale checks: how long before acting the agent last looked",
        "",
        "Each scored drift loop of a session that counts in the benchmark is placed by how many "
        "loops before its decisive point (the first command matching the case's `change` "
        "pattern, or the loop's end if none) the agent last ran a command matching the case's "
        "`look` pattern. Rates are harm on drift loops, averaged over cases; intervals are 95% "
        f"from {reps} resamples of cases and of sessions within them. Levels are pooled within "
        "each case.",
        "",
        f"Cases whose `look` pattern has an estimated recall below {percent(minimum)} are left "
        f"out ({len(left_out)} of {len(results.recall)}):",
        "",
    ]
    lines += [
        f"- {r.case}: {percent(r.recall)} of {r.unprompted_disclosures} unprompted disclosures"
        for r in left_out
    ] or ["- none"]
    for loops in (CHANGED, EVERY):
        lines += [
            "",
            f"## Harm by gap, {loops}",
            "",
            "| Model | " + " | ".join(GAPS) + " |",
            "|---|" + "---|" * len(GAPS),
        ]
        for model in models:
            row = []
            for value in GAPS:
                r = next(
                    (
                        x
                        for x in results.rates
                        if (x.model, x.level, x.factor, x.value) == (model, loops, "gap", value)
                    ),
                    None,
                )
                row.append(
                    f"{percent(r.rate)} [{percent(r.low)}, {percent(r.high)}] n={r.loops}"
                    if r
                    else "-"
                )
            lines.append(f"| {model} | " + " | ".join(row) + " |")
        lines += ["", "Within cases, against looking in the same loop:", ""]
        lines += [
            f"- {v.factor}, {v.contrast}: {v.verdict}. {v.by_model}"
            for v in results.verdicts
            if v.level == loops
        ]
    lines += [
        "",
        "## Recall of each case's look pattern",
        "",
        "| Case | Unprompted disclosures | With a look | Recall | Included |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.case} | {r.unprompted_disclosures} | {r.with_look} | {percent(r.recall)} "
        f"| {'yes' if r.included else 'no'} |"
        for r in results.recall
    ]
    return "\n".join(lines) + "\n"


def write_gaps(
    gaps: Gaps, models: list[str], out: Path, reps: int, seed: int, minimum: float
) -> GapResults:
    """Write recall.csv, rates.csv, effects.csv, by_case.csv, verdicts.csv and report.md."""
    out.mkdir(parents=True, exist_ok=True)
    results = analyse_gaps(gaps, models, reps, seed, minimum)
    write_rows(out / "recall.csv", results.recall, Recall)
    write_rows(out / "rates.csv", results.rates, Rate)
    write_rows(out / "effects.csv", results.effects, Effect)
    write_rows(out / "by_case.csv", results.by_case, CaseEffect)
    write_rows(out / "verdicts.csv", results.verdicts, FactorVerdict)
    (out / "report.md").write_text(report_gaps(results, models, reps, minimum))
    return results
