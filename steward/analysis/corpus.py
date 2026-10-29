"""Report section 2: corpus shape per domain, real-user share, harness and author mix."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from steward.analysis.figures import SERIES, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table, quantiles

MEASURES = (
    ("agent_actions", "agent actions"),
    ("user_messages", "user turns"),
    ("tokens", "tokens"),
    ("duration_minutes", "duration (min)"),
)


def shape_frame(inputs: AnalysisInputs) -> pd.DataFrame:
    frame = inputs.meta.merge(inputs.stats, on="trajectory_id", how="left")
    frame["duration_minutes"] = frame["duration_seconds"] / 60
    return frame


def mix_table(frame: pd.DataFrame, column: str, top: int = 8) -> pd.DataFrame:
    filled = frame[column].fillna("unknown")
    counts = filled.groupby(frame["domain"]).value_counts().rename("count").reset_index()
    counts = (
        counts.sort_values(["domain", "count"], ascending=[True, False]).groupby("domain").head(top)
    )
    return counts


def section(inputs: AnalysisInputs, figures: Path) -> str:
    frame = shape_frame(inputs)
    rows = []
    for domain, group in frame.groupby("domain"):
        row: dict[str, object] = {"domain": domain, "trajectories": len(group)}
        for column, label in MEASURES:
            median, p90, p99 = quantiles(group[column].tolist())
            row[f"{label} median"] = median
            row[f"{label} p90"] = p90
            row[f"{label} p99"] = p99
        row["real-user share"] = f"{100 * group.real_user.mean():.0f}%"
        rows.append(row)
    shape = pd.DataFrame(rows)

    figure, axis = plt.subplots(figsize=(6.5, 3.2))
    domains = sorted(frame.domain.unique())
    data = [np.log10(frame.loc[frame.domain == d, "agent_actions"].clip(lower=1)) for d in domains]
    parts = axis.boxplot(data, orientation="horizontal", patch_artist=True, showfliers=False)
    for patch in parts["boxes"]:
        patch.set_facecolor(SERIES[0])
        patch.set_alpha(0.35)
        patch.set_edgecolor(SERIES[0])
    axis.set_yticks(range(1, len(domains) + 1), domains)
    axis.set_xlabel("agent actions per trajectory (log10)")
    axis.set_title("Agent actions per trajectory by domain", loc="left")
    actions_png = save(figure, figures, "agent_actions_by_domain")

    real = int(frame.real_user.sum())
    lines = [
        "## 2. Corpus shape",
        "",
        f"{real:,} of {len(frame):,} ingested trajectories ({100 * real / max(1, len(frame)):.1f}%) "
        "come from sources with real users; the rest are agent-only runs. Tokens are estimated "
        "from characters (3.2 characters per token); duration is the span between the first and "
        "last timestamped step and is missing where a source has no timestamps.",
        "",
        markdown_table(shape),
        "",
        f"![Agent actions by domain]({actions_png})",
        "",
        "Harness mix per domain (top 8):",
        "",
        markdown_table(mix_table(frame, "harness")),
        "",
        "History-author mix per domain (top 8):",
        "",
        markdown_table(mix_table(frame, "history_author")),
    ]
    return "\n".join(lines)
