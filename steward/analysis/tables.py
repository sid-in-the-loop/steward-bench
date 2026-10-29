"""Markdown table and number formatting for the report."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


def markdown_table(frame: pd.DataFrame, index: bool = False) -> str:
    if index:
        frame = frame.reset_index()
    if frame.empty:
        return "No data."
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    rule = "|" + "|".join("---" for _ in frame.columns) + "|"
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, rule, *body])


def fmt(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) < 100 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value).replace("|", "/")


def percent(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def quantiles(values: Sequence[float]) -> tuple[float, float, float]:
    series = pd.Series(values, dtype=float).dropna()
    if series.empty:
        return (float("nan"),) * 3
    q = series.quantile([0.5, 0.9, 0.99])
    return float(q[0.5]), float(q[0.9]), float(q[0.99])
