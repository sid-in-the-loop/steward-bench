"""Report section 5: criterion score distributions and C1 versus C2 agreement."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from steward.analysis.figures import heatmap, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table
from steward.schema import CRITERIA


def score_frame(inputs: AnalysisInputs) -> pd.DataFrame:
    rows = []
    for result in inputs.stage_c1:
        if result.output:
            for criterion in CRITERIA:
                rows.append(
                    {
                        "stage": "c1",
                        "trajectory_id": result.trajectory_id,
                        "domain": result.domain,
                        "criterion": criterion,
                        "score": result.output.scores[criterion].score,
                    }
                )
    for c2 in inputs.stage_c2:
        for criterion in CRITERIA:
            rows.append(
                {
                    "stage": "c2",
                    "trajectory_id": c2.trajectory_id,
                    "domain": c2.domain,
                    "criterion": criterion,
                    "score": c2.output.scores[criterion].score,
                }
            )
    return pd.DataFrame(rows, columns=["stage", "trajectory_id", "domain", "criterion", "score"])


def section(inputs: AnalysisInputs, figures: Path) -> str:
    scores = score_frame(inputs)
    c1 = scores[scores.stage == "c1"]
    if c1.empty:
        return "## 5. Criterion scores\n\nNo C1 output."
    distribution = (
        c1.groupby(["criterion", "score"]).size().unstack(fill_value=0).reindex(list(CRITERIA))
    )
    mean_by_domain = (
        c1.groupby(["domain", "criterion"]).score.mean().unstack().reindex(columns=list(CRITERIA))
    )
    mean_png = save(
        heatmap(
            mean_by_domain.to_numpy(),
            list(mean_by_domain.index),
            list(CRITERIA),
            "Mean C1 score by domain and criterion",
            "{:.2f}",
        ),
        figures,
        "c1_mean_scores",
    )
    lines = [
        "## 5. Criterion scores",
        "",
        f"C1 scored {c1.trajectory_id.nunique():,} trajectories. Count of trajectories at each score:",
        "",
        markdown_table(distribution, index=True),
        "",
        f"![Mean C1 scores]({mean_png})",
        "",
    ]
    c2 = scores[scores.stage == "c2"]
    if c2.empty:
        lines.append(
            "No C2 results exist, so C1 to C2 agreement cannot be measured (see BLOCKERS.md)."
        )
        return "\n".join(lines)
    paired = c1.merge(c2, on=["trajectory_id", "criterion"], suffixes=("_c1", "_c2"))
    confusion = np.zeros((4, 4))
    for a, b in zip(paired.score_c1, paired.score_c2, strict=True):
        confusion[a, b] += 1
    rho = spearmanr(paired.score_c1, paired.score_c2).statistic if len(paired) > 2 else float("nan")
    confusion_png = save(
        heatmap(
            confusion,
            ["C1=0", "C1=1", "C1=2", "C1=3"],
            ["C2=0", "C2=1", "C2=2", "C2=3"],
            "C1 versus C2 scores, all criteria",
        ),
        figures,
        "c1_c2_confusion",
    )
    per_criterion = paired.groupby("criterion").apply(
        lambda g: pd.Series(
            {
                "pairs": len(g),
                "exact agreement": (g.score_c1 == g.score_c2).mean(),
                "spearman": spearmanr(g.score_c1, g.score_c2).statistic
                if g.score_c1.nunique() > 1 and g.score_c2.nunique() > 1
                else float("nan"),
            }
        )
    )
    lines += [
        f"On {paired.trajectory_id.nunique():,} trajectories read by both, C1 and C2 agree exactly on "
        f"{100 * (paired.score_c1 == paired.score_c2).mean():.0f}% of criterion scores; Spearman "
        f"correlation is {rho:.2f}.",
        "",
        f"![C1 versus C2]({confusion_png})",
        "",
        markdown_table(pd.DataFrame(per_criterion), index=True),
    ]
    return "\n".join(lines)
