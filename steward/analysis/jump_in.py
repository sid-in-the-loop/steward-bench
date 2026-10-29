"""Report section 7: jump_in candidates by depth, phase, moment kind and unsafe precedent."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from steward.analysis.figures import SERIES, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table, quantiles


def candidate_frame(inputs: AnalysisInputs) -> pd.DataFrame:
    deep = {r.trajectory_id for r in inputs.stage_c2}
    steps = inputs.stats.set_index("trajectory_id")["steps"]
    rows = []
    for result in inputs.stage_c2:
        for c in result.output.candidates:
            rows.append(
                {
                    "by": "c2",
                    "trajectory_id": result.trajectory_id,
                    "domain": result.domain,
                    "step": c.step,
                    "history_tokens": c.history_tokens,
                    "phase": c.phase,
                    "moment_kind": c.moment_kind,
                    "unsafe_precedent": c.unsafe_precedent,
                }
            )
    for shallow in inputs.stage_c1:
        if shallow.output is None or shallow.trajectory_id in deep:
            continue
        for candidate in shallow.output.candidates:
            rows.append(
                {
                    "by": "c1",
                    "trajectory_id": shallow.trajectory_id,
                    "domain": shallow.domain,
                    "step": candidate.step,
                    "history_tokens": None,
                    "phase": None,
                    "moment_kind": candidate.moment_kind,
                    "unsafe_precedent": candidate.unsafe_precedent,
                }
            )
    frame = pd.DataFrame(
        rows,
        columns=[
            "by",
            "trajectory_id",
            "domain",
            "step",
            "history_tokens",
            "phase",
            "moment_kind",
            "unsafe_precedent",
        ],
    )
    frame["relative_depth"] = frame.step / frame.trajectory_id.map(steps)
    return frame


def section(inputs: AnalysisInputs, figures: Path) -> str:
    frame = candidate_frame(inputs)
    if frame.empty:
        return "## 7. Jump-in candidates\n\nNo candidates."
    figure, axis = plt.subplots(figsize=(6.5, 2.6))
    axis.hist(
        frame.relative_depth.dropna().clip(0, 1), bins=20, range=(0, 1), color=SERIES[0], rwidth=0.9
    )
    axis.set_xlabel("candidate step as a fraction of trajectory length")
    axis.set_ylabel("candidates")
    axis.set_title("Depth of jump-in candidates", loc="left")
    depth_png = save(figure, figures, "jump_in_depth")
    rows = []
    for domain, group in frame.groupby("domain"):
        median, p90, _ = quantiles(group.step.tolist())
        tokens_median, tokens_p90, _ = quantiles(group.history_tokens.dropna().tolist())
        rows.append(
            {
                "domain": domain,
                "candidates": len(group),
                "step median": median,
                "step p90": p90,
                "history tokens median (C2)": tokens_median,
                "history tokens p90 (C2)": tokens_p90,
                "unsafe precedent share": f"{100 * group.unsafe_precedent.mean():.0f}%",
            }
        )
    kinds = frame.groupby(["domain", "moment_kind"]).size().unstack(fill_value=0)
    phases = (
        frame.dropna(subset=["phase"]).groupby(["domain", "phase"]).size().unstack(fill_value=0)
    )
    lines = [
        "## 7. Jump-in candidates",
        "",
        f"There are {len(frame):,} candidate steps; {100 * frame.unsafe_precedent.mean():.0f}% carry "
        "unsafe precedent in the history before them. Phase and history tokens come from C2 only.",
        "",
        markdown_table(pd.DataFrame(rows)),
        "",
        f"![Jump-in depth]({depth_png})",
        "",
        "Candidates by moment kind:",
        "",
        markdown_table(kinds, index=True),
        "",
        "Candidates by phase (C2):",
        "",
        markdown_table(phases, index=True) if not phases.empty else "No C2 candidates.",
    ]
    return "\n".join(lines)
