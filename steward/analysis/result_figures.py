"""The figures of the results report. Each returns None when its runs are missing."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle

from steward.analysis.plotting import BASELINE, GRID, MUTED, SERIES, TEXT_PRIMARY, TEXT_SECONDARY
from steward.analysis.results import (
    ASKING_OUTCOMES,
    RULE_POSITIONS,
    WORLD_ARMS,
    asking_shares,
    baseline,
    cell,
    contagion,
    design_effects,
    estimate,
    interval,
    length_bins,
    mosaic_counts,
    path_counts,
    session_values,
)
from steward.schema import AXES

UNSAFE = "#d6453d"
SAFE = "#2a78d6"
PANEL_WIDTH = 3.4


def models_of(frame: pd.DataFrame) -> list[str]:
    return sorted(frame.model.unique()) if not frame.empty else []


def short(model: str) -> str:
    return model.split("/")[-1]


def panels(models: list[str], height: float = 3.0) -> tuple[Figure, list[Axes]]:
    figure, axes = plt.subplots(
        1, len(models), figsize=(PANEL_WIDTH * len(models), height), sharey=True, squeeze=False
    )
    for axis, model in zip(axes[0], models, strict=True):
        axis.set_title(short(model), loc="left")
    return figure, list(axes[0])


def safety_profile(runs: pd.DataFrame) -> Figure | None:
    """Radar: one spoke per gridworld category, radius the mean safety score over sessions,
    one outline per model. Baseline runs."""
    base = baseline(runs)
    if base.empty:
        return None
    angles = np.linspace(0, 2 * np.pi, len(AXES), endpoint=False)
    figure, axis = plt.subplots(figsize=(5.2, 5.2), subplot_kw={"projection": "polar"})
    for color, model in zip(SERIES, models_of(base), strict=False):
        mine = base[base.model == model]
        values = [estimate(mine[mine.category == a], "safety")[0] for a in AXES]
        closed = [*values, values[0]]
        axis.plot([*angles, angles[0]], closed, color=color, linewidth=1.8, label=short(model))
        axis.fill([*angles, angles[0]], np.nan_to_num(closed), color=color, alpha=0.08)
    axis.set_xticks(angles, [a.replace("_", "\n") for a in AXES])
    axis.set_ylim(0, 1)
    axis.set_yticks([0.25, 0.5, 0.75, 1.0])
    axis.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))
    axis.set_title("Safety score by gridworld category", loc="left", pad=18)
    return figure


def history_length(runs: pd.DataFrame) -> Figure | None:
    """Two panels sharing x. x: equal-count bins of inherited history length, edges in tokens.
    Top y: task score. Bottom y: safety score. One line per model. Dashed: the original agent
    (safety everywhere; task only where the recording reports it). Baseline runs."""
    base = baseline(runs).copy()
    if base.empty or base.session.nunique() < 2:
        return None
    sessions = base.drop_duplicates("session").set_index("session").history_tokens
    codes, labels = length_bins(sessions)
    base["bin"] = base.session.map(codes)
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(6.4, 5.6), sharex=True)
    positions = range(len(labels))
    for color, model in zip(SERIES, models_of(base), strict=False):
        mine = base[base.model == model]
        for axis, value in ((top, "task"), (bottom, "safety")):
            means = [estimate(mine[mine.bin == label], value)[0] for label in labels]
            axis.plot(positions, means, color=color, marker="o", label=short(model))
    reference = base.drop_duplicates("session")
    original_safety = [
        1.0 - reference[reference.bin == label].original_unsafe.mean() for label in labels
    ]
    original_task = [reference[reference.bin == label].original_task.mean() for label in labels]
    bottom.plot(
        positions, original_safety, color=MUTED, linestyle="--", marker="s", label="original agent"
    )
    top.plot(positions, original_task, color=MUTED, linestyle="--", marker="s")
    top.set_ylabel("task score")
    bottom.set_ylabel("safety score")
    bottom.set_xticks(list(positions), labels)
    bottom.set_xlabel("inherited history, tokens (equal-count bins of sessions)")
    for axis in (top, bottom):
        axis.set_ylim(-0.03, 1.03)
    bottom.legend(loc="lower left", ncol=2)
    top.set_title("Task and safety by length of inherited history", loc="left")
    return figure


def repeat_fix_introduce(runs: pd.DataFrame) -> Figure | None:
    """One 2x2 per model. Rows: the original agent was safe or unsafe at this point.
    Columns: the tested model was safe or unsafe. Cell: number of sessions. Blue cells are
    safe outcomes for the tested model, red cells unsafe; depth of colour follows the count.
    As-recorded scenarios, baseline runs."""
    base = baseline(runs)
    models = [m for m in models_of(base) if mosaic_counts(runs, m).to_numpy().sum() > 0]
    if not models:
        return None
    names = {
        (False, False): "both safe",
        (False, True): "introduces",
        (True, False): "fixes",
        (True, True): "repeats",
    }
    figure, axes = panels(models, height=3.2)
    for axis, model in zip(axes, models, strict=True):
        counts = mosaic_counts(runs, model)
        total = max(int(counts.to_numpy().sum()), 1)
        axis.grid(False)
        for row, original in enumerate((False, True)):
            for column, tested in enumerate((False, True)):
                count = int(counts.to_numpy()[row, column])
                shade = 0.15 + 0.85 * count / total
                color = UNSAFE if tested else SAFE
                axis.add_patch(Rectangle((column, 1 - row), 1, 1, color=color, alpha=shade))
                axis.text(
                    column + 0.5,
                    1.5 - row,
                    f"{names[(original, tested)]}\n{count}",
                    ha="center",
                    va="center",
                    color=TEXT_PRIMARY,
                )
        axis.set_xlim(0, 2)
        axis.set_ylim(0, 2)
        axis.set_xticks([0.5, 1.5], ["model safe", "model unsafe"])
        axis.set_yticks([1.5, 0.5], ["original safe", "original unsafe"])
    figure.suptitle(
        "Sessions by the original agent's outcome and the tested model's",
        x=0.02,
        y=1.04,
        ha="left",
    )
    return figure


def contagion_figure(runs: pd.DataFrame) -> Figure | None:
    """One panel per model. x: the as-recorded scenario, then the same session with a line
    from the previous agent added. y: unsafe rate. Faint line per session, red where the
    session goes from safe to unsafe; bold line the mean over sessions with its interval."""
    models = [m for m in models_of(baseline(runs)) if not contagion(runs, m).empty]
    if not models:
        return None
    figure, axes = panels(models, height=3.4)
    for axis, model in zip(axes, models, strict=True):
        pairs = contagion(runs, model)
        for _, row in pairs.iterrows():
            flipped = row.left == 0 and row.right > 0
            axis.plot(
                [0, 1],
                [row.left, row.right],
                color=UNSAFE if flipped else BASELINE,
                alpha=0.55 if flipped else 0.5,
                linewidth=0.9,
            )
        left, right = interval(pairs.left), interval(pairs.right)
        axis.plot([0, 1], [left[0], right[0]], color=TEXT_PRIMARY, linewidth=2.4, marker="o")
        axis.fill_between([0, 1], [left[1], right[1]], [left[2], right[2]], color=MUTED, alpha=0.2)
        followed = int((pairs.right > 0).sum())
        axis.text(
            0.5,
            1.06,
            f"followed its predecessor in {followed} of {len(pairs)}",
            ha="center",
            color=TEXT_SECONDARY,
            fontsize=8,
        )
        axis.set_xticks([0, 1], ["as recorded", "with the previous\nagent's line"])
        axis.set_xlim(-0.2, 1.2)
        axis.set_ylim(-0.03, 1.12)
    axes[0].set_ylabel("unsafe rate")
    return figure


def designs_bite(runs: pd.DataFrame) -> Figure | None:
    """One row per design, grouped by gridworld category. x: change in unsafe rate against
    the same session's as-recorded scenario, with its interval. One dot per model."""
    effects = design_effects(runs)
    if effects.empty:
        return None
    order = (
        effects.groupby(["category", "design"])
        .change.mean()
        .reset_index()
        .sort_values(["category", "change"])
    )
    rows = list(order.design)
    figure, axis = plt.subplots(figsize=(6.6, 0.8 + 0.3 * len(rows)))
    models = sorted(effects.model.unique())
    for index, (color, model) in enumerate(zip(SERIES, models, strict=False)):
        mine = effects[effects.model == model].set_index("design").reindex(rows).dropna()
        offsets = [rows.index(d) + (index - (len(models) - 1) / 2) * 0.18 for d in mine.index]
        axis.errorbar(
            mine.change,
            offsets,
            xerr=[mine.change - mine.low, mine.high - mine.change],
            fmt="o",
            color=color,
            markersize=4,
            linewidth=1,
            label=short(model),
        )
    axis.axvline(0, color=BASELINE, linewidth=1)
    labels = [f"{d}  ({c.replace('_', ' ')})" for c, d in zip(order.category, rows, strict=True)]
    axis.set_yticks(range(len(rows)), labels)
    axis.invert_yaxis()
    axis.grid(axis="y", visible=False)
    axis.set_xlabel("change in unsafe rate against the as-recorded scenario")
    axis.legend(loc="lower right")
    axis.set_title("Effect of each design", loc="left")
    return figure


PATH_STAGES: tuple[tuple[str, ...], ...] = (
    ("took over",),
    ("asked first", "acted"),
    ("safe", "shortcut"),
    ("admitted", "claimed success"),
)
PATH_COLORS = {
    "took over": MUTED,
    "asked first": SERIES[2],
    "acted": SERIES[3],
    "safe": SAFE,
    "shortcut": UNSAFE,
    "admitted": SERIES[1],
    "claimed success": "#8f1d18",
}


def draw_path(axis: Axes, counts: dict[tuple[str, str], int]) -> None:
    """Columns of nodes sized by run count, joined by bands sized by the runs between them."""
    total = sum(n for (source, _), n in counts.items() if source == "took over")
    into: dict[str, int] = {"took over": total}
    for (_, target), number in counts.items():
        into[target] = into.get(target, 0) + number
    spans: dict[str, tuple[float, float]] = {}
    for stage in PATH_STAGES:
        top = 1.0
        for node in stage:
            height = into.get(node, 0) / total if total else 0.0
            spans[node] = (top - height, top)
            top -= height + 0.04
    used_out = {node: spans[node][1] for node in spans}
    used_in = dict(used_out)
    for column, stage in enumerate(PATH_STAGES):
        for node in stage:
            low, high = spans[node]
            if high > low:
                axis.add_patch(
                    Rectangle((column - 0.06, low), 0.12, high - low, color=PATH_COLORS[node])
                )
                axis.text(
                    column,
                    high + 0.012,
                    f"{node} {into.get(node, 0)}",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color=TEXT_SECONDARY,
                )
    for column, stage in enumerate(PATH_STAGES[:-1]):
        for source in stage:
            for target in PATH_STAGES[column + 1]:
                number = counts.get((source, target), 0)
                if not number:
                    continue
                height = number / total
                start, end = used_out[source], used_in[target]
                corners = [
                    (column + 0.06, start),
                    (column + 0.94, end),
                    (column + 0.94, end - height),
                    (column + 0.06, start - height),
                ]
                axis.add_patch(Polygon(corners, color=PATH_COLORS[target], alpha=0.3, linewidth=0))
                used_out[source] -= height
                used_in[target] -= height
    axis.set_xlim(-0.3, len(PATH_STAGES) - 0.7)
    axis.set_ylim(-0.25, 1.1)
    axis.axis("off")


def path_figure(runs: pd.DataFrame) -> Figure | None:
    """One diagram per model, left to right: took over; asked the user first or acted; ended
    safe or took the shortcut; after a shortcut, admitted it or claimed success. Node and
    band sizes are numbers of baseline runs."""
    models = [m for m in models_of(baseline(runs)) if path_counts(runs, m)]
    if not models:
        return None
    figure, axes = plt.subplots(len(models), 1, figsize=(7.2, 2.9 * len(models)), squeeze=False)
    for axis, model in zip(axes[:, 0], models, strict=True):
        draw_path(axis, path_counts(runs, model))
        axis.set_title(short(model), loc="left")
    return figure


def rule_by_length(runs: pd.DataFrame) -> Figure | None:
    """One panel per model. x: equal-count bins of inherited history length. y: safety score.
    Four lines, one per position of the user's rule in the history. Dashed: no rule given.
    Only scenarios that were run with a rule."""
    with_rule = pd.concat([cell(runs, rule=p) for p in RULE_POSITIONS])
    if with_rule.empty:
        return None
    scenarios = set(with_rule.scenario_id)
    none = baseline(runs)
    none = none[none.scenario_id.isin(scenarios)]
    sessions = with_rule.drop_duplicates("session").set_index("session").history_tokens
    codes, labels = length_bins(sessions)
    models = models_of(with_rule)
    figure, axes = panels(models)
    positions = range(len(labels))
    for axis, model in zip(axes, models, strict=True):
        for color, position in zip(SERIES, RULE_POSITIONS, strict=False):
            mine = with_rule[(with_rule.model == model) & (with_rule.rule == position)]
            if mine.empty:
                continue
            bins = mine.session.map(codes)
            means = [estimate(mine[bins == label], "safety")[0] for label in labels]
            axis.plot(positions, means, color=color, marker="o", label=position.replace("_", " "))
        mine = none[none.model == model]
        if not mine.empty:
            bins = mine.session.map(codes)
            means = [estimate(mine[bins == label], "safety")[0] for label in labels]
            axis.plot(positions, means, color=MUTED, linestyle="--", marker="s", label="no rule")
        axis.set_xticks(list(positions), labels, rotation=20)
        axis.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("safety score")
    axes[0].legend(loc="lower left")
    figure.supxlabel("inherited history, tokens (equal-count bins of sessions)", fontsize=9)
    return figure


def rule_by_oversight(runs: pd.DataFrame) -> Figure | None:
    """One panel per model. x: the user's rule absent, said recently (back), said long ago
    (front). y: safety score. Two lines: the user says the work will be reviewed, or that
    nobody will review it."""
    rules = ("none", "back", "front")
    frames = {
        (rule, level): cell(runs, rule=rule, oversight=level)
        for rule in rules
        for level in ("reviewed", "unreviewed")
    }
    if all(frame.empty for frame in frames.values()):
        return None
    models = sorted({m for frame in frames.values() for m in models_of(frame)})
    figure, axes = panels(models)
    for axis, model in zip(axes, models, strict=True):
        for color, level in ((SAFE, "reviewed"), (UNSAFE, "unreviewed")):
            estimates = []
            for rule in rules:
                frame = frames[(rule, level)]
                estimates.append(estimate(frame[frame.model == model], "safety"))
            means = [e[0] for e in estimates]
            axis.plot(range(len(rules)), means, color=color, marker="o", label=level)
            axis.fill_between(
                range(len(rules)),
                [e[1] for e in estimates],
                [e[2] for e in estimates],
                color=color,
                alpha=0.12,
            )
        axis.set_xticks(range(len(rules)), ["no rule", "said recently", "said long ago"])
        axis.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("safety score")
    axes[0].legend(loc="lower left")
    return figure


def world_frames(runs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Runs of world-change scenarios per arm. The scenario as written is the silent arm."""
    changed = runs[runs.world_change] if not runs.empty else runs
    frames = {arm: cell(changed, world=arm) for arm in WORLD_ARMS}
    if frames["silent"].empty:
        frames["silent"] = cell(changed)
    return frames


def world_arms(runs: pd.DataFrame) -> Figure | None:
    """x: the five arms. y: share of runs that take the action the changed world makes
    harmful. Bold line per model; faint lines per scenario, averaged over models."""
    frames = {arm: f.dropna(subset=["took_harmful"]) for arm, f in world_frames(runs).items()}
    present = [arm for arm in WORLD_ARMS if not frames[arm].empty]
    if len(present) < 2:
        return None
    figure, axis = plt.subplots(figsize=(6.6, 3.8))
    positions = {arm: index for index, arm in enumerate(WORLD_ARMS)}
    everything = pd.concat([frames[arm].assign(arm=arm) for arm in present])
    everything = everything.astype({"took_harmful": float})
    per_scenario = everything.pivot_table(index="scenario_id", columns="arm", values="took_harmful")
    for _, row in per_scenario.iterrows():
        points = [(positions[arm], row[arm]) for arm in present if not pd.isna(row.get(arm))]
        axis.plot(*zip(*points, strict=True), color=GRID, linewidth=0.8)
    for color, model in zip(SERIES, sorted(everything.model.unique()), strict=False):
        mine = everything[everything.model == model]
        arms = [arm for arm in present if not mine[mine.arm == arm].empty]
        means = [estimate(mine[mine.arm == arm], "took_harmful")[0] for arm in arms]
        axis.plot(
            [positions[a] for a in arms],
            means,
            color=color,
            marker="o",
            linewidth=2,
            label=short(model),
        )
    axis.set_xticks(range(len(WORLD_ARMS)), list(WORLD_ARMS))
    axis.set_ylim(-0.03, 1.03)
    axis.set_ylabel("share of runs taking the harmful action")
    axis.legend(loc="upper right")
    axis.set_title("Taking the action the changed world makes harmful", loc="left")
    return figure


NOTICED = (
    ("saw the change, stayed safe", True, False, SAFE),
    ("saw the change, took the action", True, True, "#8f1d18"),
    ("never saw it, took the action", False, True, UNSAFE),
    ("never saw it, stayed safe", False, False, BASELINE),
)


def world_noticed(runs: pd.DataFrame) -> Figure | None:
    """x: the silent and mid-run arms, grouped by model. y: share of runs, stacked by whether
    the model saw the change before its first state-changing action and whether it took the
    harmful action."""
    frames = world_frames(runs)
    bars = []
    for arm in ("silent", "midrun"):
        frame = frames[arm].dropna(subset=["took_harmful", "saw_change_first"])
        for model in models_of(frame):
            bars.append((f"{short(model)}\n{arm}", frame[frame.model == model]))
    if not bars:
        return None
    figure, axis = plt.subplots(figsize=(1.6 + 1.1 * len(bars), 3.8))
    bottoms = np.zeros(len(bars))
    for label, saw, took, color in NOTICED:
        shares = np.array(
            [
                float(
                    (
                        (frame.saw_change_first.astype(bool) == saw)
                        & (frame.took_harmful.astype(bool) == took)
                    ).mean()
                )
                for _, frame in bars
            ]
        )
        axis.bar(range(len(bars)), shares, bottom=bottoms, color=color, label=label, width=0.65)
        bottoms += shares
    axis.set_xticks(range(len(bars)), [name for name, _ in bars])
    axis.set_ylabel("share of runs")
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    return figure


def pressure_by_responder(runs: pd.DataFrame) -> Figure | None:
    """One panel per model. x: the user's opening message removed or present. y: unsafe
    rate. Two lines: fixed replies to the agent's questions, or a simulated user."""
    frames = {
        (opening, simulated): cell(runs, opening=opening, simulated=simulated)
        for opening in ("removed", "present")
        for simulated in (False, True)
    }
    scenarios = set.union(
        set(frames[("removed", False)].scenario_id)
        if not frames[("removed", False)].empty
        else set(),
        set(frames[("present", True)].scenario_id)
        if not frames[("present", True)].empty
        else set(),
        set(frames[("removed", True)].scenario_id)
        if not frames[("removed", True)].empty
        else set(),
    )
    if not scenarios:
        return None
    models = sorted({m for frame in frames.values() for m in models_of(frame)})
    figure, axes = panels(models)
    for axis, model in zip(axes, models, strict=True):
        for color, simulated, label in (
            (SERIES[0], False, "fixed replies"),
            (SERIES[1], True, "simulated user"),
        ):
            means = []
            for opening in ("removed", "present"):
                frame = frames[(opening, simulated)]
                frame = frame[(frame.model == model) & frame.scenario_id.isin(scenarios)]
                means.append(estimate(frame, "unsafe")[0] if not frame.empty else float("nan"))
            axis.plot([0, 1], means, color=color, marker="o", label=label)
        axis.set_xticks([0, 1], ["opening removed", "opening present"])
        axis.set_xlim(-0.2, 1.2)
        axis.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("unsafe rate")
    axes[0].legend(loc="upper left")
    return figure


def asking(runs: pd.DataFrame) -> Figure | None:
    """x: models. y: share of baseline runs, stacked: acted without asking and ended safe or
    unsafe, asked the user first and ended safe or unsafe, never completed."""
    shares = asking_shares(runs)
    if shares.empty:
        return None
    colors = [SAFE, UNSAFE, SERIES[2], SERIES[1], BASELINE]
    figure, axis = plt.subplots(figsize=(1.8 + 1.2 * len(shares), 3.8))
    bottoms = np.zeros(len(shares))
    for color, outcome in zip(colors, ASKING_OUTCOMES, strict=True):
        values = shares[outcome].to_numpy(dtype=float)
        axis.bar(range(len(shares)), values, bottom=bottoms, color=color, label=outcome, width=0.6)
        bottoms += values
    axis.set_xticks(range(len(shares)), [short(m) for m in shares.model])
    axis.set_ylabel("share of baseline runs")
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=2)
    return figure


def looked_first_figure(runs: pd.DataFrame) -> Figure | None:
    """x: models, three bars each: share of runs that saw the planted result before their
    first state-changing action; unsafe rate after looking; unsafe rate without looking.
    Scenarios whose planted result contradicts what the previous agent said."""
    base = baseline(runs)
    base = base[base.predecessor_line & base.planted_results] if not base.empty else base
    models = models_of(base)
    if not models:
        return None
    figure, axis = plt.subplots(figsize=(1.8 + 1.6 * len(models), 3.4))
    looked, blind = base[base.looked_first], base[~base.looked_first]
    bars = (
        (
            "looked first",
            MUTED,
            [estimate(base[base.model == m], "looked_first")[0] for m in models],
        ),
        ("unsafe after looking", SAFE, [unsafe_rate(looked[looked.model == m]) for m in models]),
        ("unsafe without looking", UNSAFE, [unsafe_rate(blind[blind.model == m]) for m in models]),
    )
    for offset, (label, color, heights) in enumerate(bars):
        axis.bar(
            np.arange(len(models)) + (offset - 1) * 0.26, heights, 0.24, color=color, label=label
        )
    axis.set_xticks(range(len(models)), [short(m) for m in models])
    axis.set_ylim(0, 1.03)
    axis.set_ylabel("share of runs")
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
    return figure


def unsafe_rate(frame: pd.DataFrame) -> float:
    return (
        float(session_values(frame.astype({"unsafe": float}), "unsafe").mean())
        if len(frame)
        else 0.0
    )
