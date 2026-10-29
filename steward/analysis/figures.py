"""Shared figure style and helpers for the Phase 1 report."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "steward_blue", ["#f4f8fd", "#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"]
)


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": BASELINE,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.titlecolor": TEXT_PRIMARY,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "font.size": 9,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )


def save(figure: Figure, directory: Path, name: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    figure.savefig(path)
    plt.close(figure)
    return f"figures/{name}.png"


def heatmap(
    values: np.ndarray,
    rows: Sequence[str],
    columns: Sequence[str],
    title: str,
    value_format: str = "{:.0f}",
) -> Figure:
    figure, axis = plt.subplots(figsize=(1.2 + 0.9 * len(columns), 0.9 + 0.45 * len(rows)))
    axis.grid(False)
    vmax = float(values.max()) if values.size and values.max() > 0 else 1.0
    axis.imshow(values, cmap=SEQUENTIAL, vmin=0, vmax=vmax, aspect="auto")
    axis.set_xticks(range(len(columns)), columns, rotation=35, ha="right")
    axis.set_yticks(range(len(rows)), rows)
    for i in range(len(rows)):
        for j in range(len(columns)):
            value = values[i, j]
            color = SURFACE if value > 0.55 * vmax else TEXT_PRIMARY
            axis.text(
                j, i, value_format.format(value), ha="center", va="center", fontsize=7, color=color
            )
    axis.set_title(title, loc="left")
    return figure


def horizontal_bars(
    labels: Sequence[str], values: Sequence[float], title: str, xlabel: str
) -> Figure:
    figure, axis = plt.subplots(figsize=(6.5, 0.6 + 0.3 * max(1, len(labels))))
    positions = np.arange(len(labels))
    axis.barh(positions, values, color=SERIES[0], height=0.6)
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.set_xlabel(xlabel)
    axis.grid(axis="y", visible=False)
    for position, value in zip(range(len(values)), values, strict=True):
        axis.text(value, position, f" {value:,.0f}", va="center", fontsize=7, color=TEXT_SECONDARY)
    axis.set_title(title, loc="left")
    return figure
