"""Report section 1: funnel per source and domain, and rejection reasons for A and B."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from steward.analysis.figures import heatmap, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table, percent

B_REASON_CATEGORIES = (
    (
        "no clear task",
        r"chit|chat|no (clear |real )?task|unclear task|loop|broken|trivial|question",
    ),
    ("no safety moment", r"no (safety|consequential)|safety-relevant|routine|read-only|only reads"),
    (
        "not rebuildable offline",
        r"rebuil|offline|production|credential|private|live|cloud|paid|proprietary",
    ),
    ("not script-checkable", r"check|verif|script"),
)
STAGES = ["ingested", "stage_a", "stage_b_in", "stage_b_yes", "stage_c1", "stage_c2", "ranked"]


def b_reason_category(reason: str) -> str:
    for name, pattern in B_REASON_CATEGORIES:
        if re.search(pattern, reason, re.IGNORECASE):
            return name
    return "other"


def funnel_frame(inputs: AnalysisInputs, ranked_ids: set[str]) -> pd.DataFrame:
    meta = inputs.meta[["trajectory_id", "source", "domain"]].copy()
    passed_a = {r.trajectory_id for r in inputs.stage_a if r.passed}
    b_in = {r.trajectory_id for r in inputs.stage_b}
    b_yes = {r.trajectory_id for r in inputs.stage_b if r.output and r.output.decision == "YES"}
    c1 = {r.trajectory_id for r in inputs.stage_c1 if r.output}
    c2 = {r.trajectory_id for r in inputs.stage_c2}
    final_domain = {r.trajectory_id: r.domain for r in inputs.stage_b}
    meta["domain"] = [
        final_domain.get(t, d) for t, d in zip(meta.trajectory_id, meta.domain, strict=True)
    ]
    meta["ingested"] = 1
    for column, ids in (
        ("stage_a", passed_a),
        ("stage_b_in", b_in),
        ("stage_b_yes", b_yes),
        ("stage_c1", c1),
        ("stage_c2", c2),
        ("ranked", ranked_ids),
    ):
        meta[column] = meta.trajectory_id.isin(ids).astype(int)
    return meta


def section(inputs: AnalysisInputs, ranked_ids: set[str], figures: Path) -> str:
    frame = funnel_frame(inputs, ranked_ids)
    by_source = frame.groupby("source")[STAGES].sum().sort_values("ingested", ascending=False)
    by_domain = frame.groupby("domain")[STAGES].sum().sort_values("ingested", ascending=False)
    figure = heatmap(
        np.log10(by_domain[STAGES].to_numpy(dtype=float) + 1),
        list(by_domain.index),
        STAGES,
        "Funnel by domain (cell text is log10 of count + 1)",
        "{:.1f}",
    )
    funnel_png = save(figure, figures, "funnel_by_domain")

    a = pd.DataFrame(
        [{"source": r.source, "check": r.failed_check} for r in inputs.stage_a if not r.passed]
    )
    a_table = (
        a.groupby(["source", "check"]).size().unstack(fill_value=0)
        if not a.empty
        else pd.DataFrame()
    )
    b_no = [r for r in inputs.stage_b if r.output and r.output.decision == "NO"]
    b_cats = pd.Series(
        [b_reason_category(r.output.reason) for r in b_no if r.output]
    ).value_counts()
    b_errors = sum(1 for r in inputs.stage_b if r.output is None)

    total = int(frame.ingested.sum())
    ranked = int(frame.ranked.sum())
    lines = [
        "## 1. Funnel",
        "",
        f"Of {total:,} ingested trajectories, {int(frame.stage_a.sum()):,} pass Stage A "
        f"({percent(frame.stage_a.sum(), total)}), {int(frame.stage_b_yes.sum()):,} pass Stage B, "
        f"{int(frame.stage_c1.sum()):,} are scored by C1, {int(frame.stage_c2.sum()):,} by C2, and "
        f"{ranked:,} appear in at least one ranked list.",
        "",
        f"![Funnel by domain]({funnel_png})",
        "",
        "Funnel by domain (domain after Stage B assignment):",
        "",
        markdown_table(by_domain, index=True),
        "",
        "Funnel by source:",
        "",
        markdown_table(by_source, index=True),
        "",
        "Stage A rejections by first failed check (A1 length, A2 state change, A3 identifiable "
        "world, A4 real interaction, A5 complete history):",
        "",
        markdown_table(a_table, index=True) if not a_table.empty else "No rejections.",
        "",
        f"Stage B rejections by reason category ({len(b_no):,} NO answers, {b_errors:,} unparsable "
        "replies). Categories are assigned by keyword rules on the model's reason text:",
        "",
        markdown_table(b_cats.rename_axis("category").reset_index(name="count"))
        if len(b_cats)
        else "None.",
    ]
    return "\n".join(lines)
