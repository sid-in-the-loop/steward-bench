"""The figures of the results report. Each returns None when its runs are missing."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle

from steward.analysis.plotting import BASELINE, GRID, MUTED, SERIES, TEXT_PRIMARY, TEXT_SECONDARY
from steward.analysis.result_tables import (
    NO_RULE,
    NOTICED_ARMS,
    OPENINGS,
    ORIGINAL,
    OVERSIGHT_LEVELS,
    OVERSIGHT_RULES,
    category_table,
    history_length_table,
    looked_first_table,
    pressure_table,
    rule_by_length_table,
    rule_by_oversight_table,
    world_arm_runs,
    world_arm_table,
    world_noticed_table,
)
from steward.analysis.results import (
    ASKING_OUTCOMES,
    RULE_POSITIONS,
    WORLD_ARMS,
    asking_shares,
    baseline,
    contagion,
    design_effects,
    interval,
    mosaic_counts,
    path_counts,
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
    scores = category_table(base, "safety").set_index(["model", "category"]).safety
    for color, model in zip(SERIES, models_of(base), strict=False):
        values = [float(scores.get((model, a), np.nan)) for a in AXES]
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
    labels, table = history_length_table(runs)
    if table.empty:
        return None
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(6.4, 5.6), sharex=True)
    positions = range(len(labels))
    models = sorted(set(table.series) - {ORIGINAL})
    for color, series in zip([*SERIES[: len(models)], MUTED], [*models, ORIGINAL], strict=False):
        mine = table[table.series == series].set_index("bin").reindex(labels)
        dashed = series == ORIGINAL
        for axis, value in ((top, "task"), (bottom, "safety")):
            axis.plot(
                positions,
                mine[value].astype(float),
                color=color,
                linestyle="--" if dashed else "-",
                marker="s" if dashed else "o",
                label=ORIGINAL if dashed else short(series),
            )
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
    labels, table = rule_by_length_table(runs)
    if table.empty:
        return None
    models = sorted(table.model.unique())
    figure, axes = panels(models)
    positions = range(len(labels))
    lines = [
        *zip(RULE_POSITIONS, SERIES, strict=False),
        (NO_RULE, MUTED),
    ]
    for axis, model in zip(axes, models, strict=True):
        for rule, color in lines:
            mine = table[(table.model == model) & (table.rule == rule)]
            if mine.empty:
                continue
            dashed = rule == NO_RULE
            axis.plot(
                positions,
                mine.set_index("bin").safety.reindex(labels).astype(float),
                color=color,
                linestyle="--" if dashed else "-",
                marker="s" if dashed else "o",
                label="no rule" if dashed else rule.replace("_", " "),
            )
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
    table = rule_by_oversight_table(runs)
    if table.empty:
        return None
    models = sorted(table.model.unique())
    figure, axes = panels(models)
    spots = range(len(OVERSIGHT_RULES))
    for axis, model in zip(axes, models, strict=True):
        for color, level in zip((SAFE, UNSAFE), OVERSIGHT_LEVELS, strict=True):
            mine = table[(table.model == model) & (table.oversight == level)]
            mine = mine.set_index("rule").reindex(list(OVERSIGHT_RULES))
            axis.plot(spots, mine.safety.astype(float), color=color, marker="o", label=level)
            axis.fill_between(
                spots, mine.low.astype(float), mine.high.astype(float), color=color, alpha=0.12
            )
        axis.set_xticks(spots, ["no rule", "said recently", "said long ago"])
        axis.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("safety score")
    axes[0].legend(loc="lower left")
    return figure


def world_arms(runs: pd.DataFrame) -> Figure | None:
    """x: the five arms. y: share of runs that take the action the changed world makes
    harmful. Bold line per model; faint lines per scenario, averaged over models."""
    everything = world_arm_runs(runs)
    present = [arm for arm in WORLD_ARMS if not everything.empty and (everything.arm == arm).any()]
    if len(present) < 2:
        return None
    figure, axis = plt.subplots(figsize=(6.6, 3.8))
    positions = {arm: index for index, arm in enumerate(WORLD_ARMS)}
    per_scenario = everything.pivot_table(index="scenario_id", columns="arm", values="took_harmful")
    for _, row in per_scenario.iterrows():
        points = [(positions[arm], row[arm]) for arm in present if not pd.isna(row.get(arm))]
        axis.plot(*zip(*points, strict=True), color=GRID, linewidth=0.8)
    table = world_arm_table(runs)
    for color, model in zip(SERIES, sorted(table.model.unique()), strict=False):
        mine = table[table.model == model].set_index("arm").took_harmful
        arms = [arm for arm in present if arm in mine.index]
        axis.plot(
            [positions[a] for a in arms],
            [mine[a] for a in arms],
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
    ("saw the change, stayed safe", "saw_safe", SAFE),
    ("saw the change, took the action", "saw_took", "#8f1d18"),
    ("never saw it, took the action", "unseen_took", UNSAFE),
    ("never saw it, stayed safe", "unseen_safe", BASELINE),
)


def world_noticed(runs: pd.DataFrame) -> Figure | None:
    """x: the silent and mid-run arms, grouped by model. y: share of runs, stacked by whether
    the model saw the change before its first state-changing action and whether it took the
    harmful action."""
    table = world_noticed_table(runs)
    if table.empty:
        return None
    order = {arm: index for index, arm in enumerate(NOTICED_ARMS)}
    table = table.assign(order=table.arm.map(order)).sort_values(["order", "model"])
    figure, axis = plt.subplots(figsize=(1.6 + 1.1 * len(table), 3.8))
    bottoms = np.zeros(len(table))
    for label, part, color in NOTICED:
        shares = table[part].to_numpy(dtype=float)
        axis.bar(range(len(table)), shares, bottom=bottoms, color=color, label=label, width=0.65)
        bottoms += shares
    names = [f"{short(m)}\n{arm}" for m, arm in zip(table.model, table.arm, strict=True)]
    axis.set_xticks(range(len(table)), names)
    axis.set_ylabel("share of runs")
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    return figure


def pressure_by_responder(runs: pd.DataFrame) -> Figure | None:
    """One panel per model. x: the user's opening message removed or present. y: unsafe
    rate. Two lines: fixed replies to the agent's questions, or a simulated user."""
    table = pressure_table(runs)
    if table.empty:
        return None
    models = sorted(table.model.unique())
    figure, axes = panels(models)
    for axis, model in zip(axes, models, strict=True):
        for color, simulated, label in (
            (SERIES[0], False, "fixed replies"),
            (SERIES[1], True, "simulated user"),
        ):
            mine = table[(table.model == model) & (table.simulated == simulated)]
            means = mine.set_index("opening").unsafe.reindex(list(OPENINGS)).astype(float)
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
    table = looked_first_table(runs)
    if table.empty:
        return None
    figure, axis = plt.subplots(figsize=(1.8 + 1.6 * len(table), 3.4))
    bars = (
        ("looked first", MUTED, "looked"),
        ("unsafe after looking", SAFE, "unsafe_if_looked"),
        ("unsafe without looking", UNSAFE, "unsafe_if_not"),
    )
    for offset, (label, color, column) in enumerate(bars):
        heights = np.nan_to_num(table[column].to_numpy(dtype=float))
        axis.bar(
            np.arange(len(table)) + (offset - 1) * 0.26, heights, 0.24, color=color, label=label
        )
    axis.set_xticks(range(len(table)), [short(m) for m in table.model])
    axis.set_ylim(0, 1.03)
    axis.set_ylabel("share of runs")
    axis.grid(axis="x", visible=False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
    return figure
