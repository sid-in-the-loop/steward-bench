"""The numbers behind each results figure, as tables.

The report's figures and the page exporter both draw from these, so a figure and its
counterpart on the page always show the same values. A table is empty when its runs are
missing. Cells that are compared with each other are restricted to the scenarios a model ran
in all of them.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping

import pandas as pd

from steward.analysis.results import (
    RULE_POSITIONS,
    WORLD_ARMS,
    baseline,
    cell,
    estimate,
    length_bins,
)

ORIGINAL = "original agent"
NO_RULE = "none"
OVERSIGHT_RULES = (NO_RULE, "back", "front")
OVERSIGHT_LEVELS = ("reviewed", "unreviewed")
OPENINGS = ("removed", "present")
NOTICED_ARMS = ("silent", "midrun")
NOTICED_PARTS = ("saw_safe", "saw_took", "unseen_took", "unseen_safe")


def mean_of(frame: pd.DataFrame, value: str) -> float:
    """A column's mean over sessions; not a number when there are no runs."""
    return estimate(frame, value)[0] if len(frame) else float("nan")


def matched[Key: Hashable](frames: Mapping[Key, pd.DataFrame]) -> dict[Key, pd.DataFrame]:
    """Each frame cut down to the scenarios its model ran in every frame that has runs of
    that model, so the cells are compared on the same scenarios."""
    shared: dict[str, set[str]] = {}
    for frame in frames.values():
        if frame.empty:
            continue
        for model, mine in frame.groupby("model"):
            ids = set(mine.scenario_id)
            shared[str(model)] = shared[str(model)] & ids if str(model) in shared else ids
    result = {}
    for key, frame in frames.items():
        if frame.empty:
            result[key] = frame
            continue
        keep = [
            scenario in shared.get(str(model), set())
            for model, scenario in zip(frame.model, frame.scenario_id, strict=True)
        ]
        result[key] = frame[keep]
    return result


def session_bins(frame: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """Equal-count bins of the frame's sessions by inherited history length."""
    sessions = frame.drop_duplicates("session").set_index("session").history_tokens
    return length_bins(sessions)


def history_length_table(runs: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """Per model and length bin: task score, task completion, safety score and safe rate on
    the baseline. The original agent is one more series, with its task where recorded."""
    base = baseline(runs)
    if base.empty or base.session.nunique() < 2:
        return [], pd.DataFrame()
    base = base.copy()
    codes, labels = session_bins(base)
    base["bin"] = base.session.map(codes)
    reference = base.drop_duplicates("session")
    rows = []
    for label in labels:
        for model, mine in base[base.bin == label].groupby("model"):
            rows.append(
                {
                    "series": model,
                    "bin": label,
                    "task": mean_of(mine, "task"),
                    "task_complete": mean_of(mine, "task_complete"),
                    "safety": mean_of(mine, "safety"),
                    "safe": mean_of(mine, "safe"),
                }
            )
        inside = reference[reference.bin == label]
        original_safe = 1.0 - float(inside.original_unsafe.mean())
        original_task = float(inside.original_task.astype(float).mean())
        rows.append(
            {
                "series": ORIGINAL,
                "bin": label,
                "task": original_task,
                "task_complete": original_task,
                "safety": original_safe,
                "safe": original_safe,
            }
        )
    return labels, pd.DataFrame(rows)


def category_table(frame: pd.DataFrame, value: str) -> pd.DataFrame:
    """Per model and gridworld category, a column's mean over sessions."""
    rows = [
        {"model": model, "category": category, value: mean_of(mine, value)}
        for (model, category), mine in frame.groupby(["model", "category"])
    ]
    return pd.DataFrame(rows, columns=["model", "category", value])


def looked_first_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Per model, on baseline scenarios whose planted result contradicts the previous agent:
    the share of runs that saw the result before the first state-changing action, and the
    unsafe rate with and without having seen it."""
    base = baseline(runs)
    columns = ["model", "scenarios", "looked", "unsafe_if_looked", "unsafe_if_not"]
    if base.empty:
        return pd.DataFrame(columns=columns)
    base = base[base.predecessor_line & base.planted_results]
    rows = [
        {
            "model": model,
            "scenarios": len(mine),
            "looked": mean_of(mine, "looked_first"),
            "unsafe_if_looked": mean_of(mine[mine.looked_first], "unsafe"),
            "unsafe_if_not": mean_of(mine[~mine.looked_first], "unsafe"),
        }
        for model, mine in base.groupby("model")
    ]
    return pd.DataFrame(rows, columns=columns)


def rule_by_length_table(runs: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """Per model, rule position and length bin: safety score and safe rate. The rule `none`
    is the baseline on the same scenarios."""
    frames: dict[str, pd.DataFrame] = {p: cell(runs, rule=p) for p in RULE_POSITIONS}
    if all(frame.empty for frame in frames.values()):
        return [], pd.DataFrame()
    frames[NO_RULE] = baseline(runs)
    frames = matched(frames)
    with_rule = pd.concat([frames[p] for p in RULE_POSITIONS])
    codes, labels = session_bins(with_rule)
    rows = []
    for rule, frame in frames.items():
        if frame.empty:
            continue
        bins = frame.session.map(codes)
        for model in sorted(with_rule.model.unique()):
            for label in labels:
                mine = frame[(frame.model == model) & (bins == label)]
                if len(mine):
                    rows.append(
                        {
                            "model": model,
                            "rule": rule,
                            "bin": label,
                            "safety": mean_of(mine, "safety"),
                            "safe": mean_of(mine, "safe"),
                        }
                    )
    return labels, pd.DataFrame(rows)


def rule_by_oversight_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Per model, rule setting (none, back, front) and oversight statement: safety score
    with its interval, and safe rate."""
    frames = matched(
        {
            (rule, level): cell(runs, rule=rule, oversight=level)
            for rule in OVERSIGHT_RULES
            for level in OVERSIGHT_LEVELS
        }
    )
    rows = []
    for (rule, level), frame in frames.items():
        for model, mine in frame.groupby("model") if not frame.empty else ():
            safety, low, high = estimate(mine, "safety")
            rows.append(
                {
                    "model": model,
                    "rule": rule,
                    "oversight": level,
                    "safety": safety,
                    "low": low,
                    "high": high,
                    "safe": mean_of(mine, "safe"),
                }
            )
    return pd.DataFrame(rows)


def world_frames(runs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Runs of world-change scenarios per arm. The scenario as written is the silent arm."""
    changed = runs[runs.world_change] if not runs.empty else runs
    frames = {arm: cell(changed, world=arm) for arm in WORLD_ARMS}
    if frames["silent"].empty:
        frames["silent"] = cell(changed)
    return frames


def world_arm_runs(runs: pd.DataFrame) -> pd.DataFrame:
    """Every world-arm run that records whether the harmful action was taken, with its arm."""
    frames = matched(
        {arm: f.dropna(subset=["took_harmful"]) for arm, f in world_frames(runs).items()}
    )
    present = [frames[arm].assign(arm=arm) for arm in WORLD_ARMS if not frames[arm].empty]
    if not present:
        return pd.DataFrame()
    return pd.concat(present).astype({"took_harmful": float})


def world_arm_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Per model and arm, the share of runs that take the action the change makes harmful."""
    everything = world_arm_runs(runs)
    rows = [
        {"model": model, "arm": arm, "took_harmful": mean_of(mine, "took_harmful")}
        for (model, arm), mine in (
            everything.groupby(["model", "arm"]) if not everything.empty else ()
        )
    ]
    return pd.DataFrame(rows, columns=["model", "arm", "took_harmful"])


def world_noticed_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Per model, for the silent and mid-run arms: shares of runs by whether the model saw
    the change before its first state-changing action and whether it took the action."""
    frames = world_frames(runs)
    rows = []
    for arm in NOTICED_ARMS:
        frame = frames[arm].dropna(subset=["took_harmful", "saw_change_first"])
        for model, mine in frame.groupby("model") if not frame.empty else ():
            saw = mine.saw_change_first.astype(bool)
            took = mine.took_harmful.astype(bool)
            rows.append(
                {
                    "model": model,
                    "arm": arm,
                    "runs": len(mine),
                    "saw_safe": float((saw & ~took).mean()),
                    "saw_took": float((saw & took).mean()),
                    "unseen_took": float((~saw & took).mean()),
                    "unseen_safe": float((~saw & ~took).mean()),
                }
            )
    return pd.DataFrame(rows, columns=["model", "arm", "runs", *NOTICED_PARTS])


def pressure_table(runs: pd.DataFrame) -> pd.DataFrame:
    """Per model, opening (removed or present) and responder (fixed replies or a simulated
    user): the unsafe rate. Empty unless some pass changes the opening or the responder."""
    frames = {
        (opening, simulated): cell(runs, opening=opening, simulated=simulated)
        for opening in OPENINGS
        for simulated in (False, True)
    }
    if all(frame.empty for key, frame in frames.items() if key != ("present", False)):
        return pd.DataFrame()
    rows = [
        {
            "model": model,
            "opening": opening,
            "simulated": simulated,
            "unsafe": mean_of(mine, "unsafe"),
        }
        for (opening, simulated), frame in matched(frames).items()
        for model, mine in (frame.groupby("model") if not frame.empty else ())
    ]
    return pd.DataFrame(rows)
