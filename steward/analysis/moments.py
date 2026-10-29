"""Report section 3: moment-kind frequency per domain and co-occurrence."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from steward.analysis.figures import heatmap, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table
from steward.schema import MOMENT_KINDS


def kind_sets(inputs: AnalysisInputs) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for result in inputs.stage_a:
        kinds = {t.kind for t in result.tags}
        flags: dict[str, object] = {k: k in kinds for k in MOMENT_KINDS}
        rows.append(
            {
                "trajectory_id": result.trajectory_id,
                "domain": result.domain,
                **flags,
            }
        )
    return pd.DataFrame(rows)


def model_kinds(inputs: AnalysisInputs) -> pd.Series:
    counts: dict[str, int] = {}
    for result in inputs.stage_b:
        if result.output:
            for kind in set(result.output.moment_kinds):
                counts[kind] = counts.get(kind, 0) + 1
    return pd.Series(counts, dtype=int).sort_values(ascending=False)


def section(inputs: AnalysisInputs, figures: Path) -> str:
    frame = kind_sets(inputs)
    if frame.empty:
        return "## 3. Moment kinds\n\nNo Stage A output."
    kinds = [k for k in MOMENT_KINDS if frame[k].any()]
    share = frame.groupby("domain")[kinds].mean() * 100
    share_png = save(
        heatmap(
            share.to_numpy(),
            list(share.index),
            kinds,
            "Share of trajectories with each pattern-tagged kind (%)",
        ),
        figures,
        "moment_kinds_by_domain",
    )
    matrix = frame[kinds].to_numpy(dtype=float)
    together = matrix.T @ matrix
    present = np.diag(together).copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        conditional = np.where(present[:, None] > 0, 100 * together / present[:, None], 0)
    cooc_png = save(
        heatmap(
            conditional,
            kinds,
            kinds,
            "Co-occurrence: % of trajectories with row kind that also have column kind",
        ),
        figures,
        "moment_kind_cooccurrence",
    )
    by_model = model_kinds(inputs)
    top = share.mean(axis=0).sort_values(ascending=False)
    lines = [
        "## 3. Moment kinds",
        "",
        f"Pattern tags from Stage A cover {len(kinds)} of the {len(MOMENT_KINDS)} kinds. The most "
        f"common kind overall is {top.index[0]} (mean share across domains {top.iloc[0]:.0f}%). "
        "user_report and effort_shortcut are rarely visible to patterns, so the Stage B model's "
        "moment kinds are shown alongside.",
        "",
        f"![Moment kinds by domain]({share_png})",
        "",
        markdown_table(share.round(1), index=True),
        "",
        f"![Moment kind co-occurrence]({cooc_png})",
        "",
        "Moment kinds named by the Stage B model (trajectories mentioning each):",
        "",
        markdown_table(by_model.rename_axis("kind").reset_index(name="trajectories"))
        if len(by_model)
        else "No Stage B output.",
    ]
    return "\n".join(lines)
