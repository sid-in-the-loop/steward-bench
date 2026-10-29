"""Report section 6: domain by axis coverage of trajectories scoring at least 2 and exactly 3."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from steward.analysis.figures import heatmap, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table
from steward.filter.rank import combine
from steward.schema import AXES


def final_scores(inputs: AnalysisInputs) -> pd.DataFrame:
    rows = []
    for scored in combine(inputs.stage_c1, inputs.stage_c2):
        for axis in AXES:
            rows.append({"domain": scored.domain, "axis": axis, "score": scored.scores[axis].score})
    return pd.DataFrame(rows, columns=["domain", "axis", "score"])


def section(inputs: AnalysisInputs, figures: Path) -> str:
    frame = final_scores(inputs)
    if frame.empty:
        return "## 6. Axis coverage\n\nNo scored trajectories."
    at_least_two = frame[frame.score >= 2].groupby(["domain", "axis"]).size().unstack(fill_value=0)
    exactly_three = frame[frame.score == 3].groupby(["domain", "axis"]).size().unstack(fill_value=0)
    domains = sorted(frame.domain.unique())
    at_least_two = at_least_two.reindex(index=domains, columns=list(AXES), fill_value=0)
    exactly_three = exactly_three.reindex(index=domains, columns=list(AXES), fill_value=0)
    two_png = save(
        heatmap(at_least_two.to_numpy(), domains, list(AXES), "Trajectories scoring at least 2"),
        figures,
        "coverage_at_least_2",
    )
    three_png = save(
        heatmap(exactly_three.to_numpy(), domains, list(AXES), "Trajectories scoring 3"),
        figures,
        "coverage_exactly_3",
    )
    weakest = at_least_two.sum(axis=0).idxmin()
    lines = [
        "## 6. Axis coverage",
        "",
        "Final scores use C2 where available and C1 otherwise. The thinnest axis overall is "
        f"{weakest}, with {int(at_least_two[weakest].sum())} trajectories scoring at least 2.",
        "",
        f"![At least 2]({two_png})",
        "",
        markdown_table(at_least_two, index=True),
        "",
        f"![Exactly 3]({three_png})",
        "",
        markdown_table(exactly_three, index=True),
    ]
    return "\n".join(lines)
