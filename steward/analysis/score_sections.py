"""Report sections 5 to 8, about the scores: criterion scores and C1 to C2 agreement, axis
coverage per domain, jump-in candidates, and limitations backed by numbers.
"""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from steward.analysis.inputs import AnalysisInputs
from steward.analysis.plotting import SERIES, heatmap, markdown_table, quantiles, save
from steward.filter.render import CHARS_PER_TOKEN
from steward.schema import AXES, CRITERIA


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


def criteria_section(inputs: AnalysisInputs, figures: Path) -> str:
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


def final_scores(inputs: AnalysisInputs) -> pd.DataFrame:
    scores = {r.trajectory_id: (r.domain, r.output.scores) for r in inputs.stage_c1 if r.output}
    scores.update({r.trajectory_id: (r.domain, r.output.scores) for r in inputs.stage_c2})
    rows = []
    for domain, by_criterion in scores.values():
        for axis in AXES:
            rows.append({"domain": domain, "axis": axis, "score": by_criterion[axis].score})
    return pd.DataFrame(rows, columns=["domain", "axis", "score"])


def coverage_section(inputs: AnalysisInputs, figures: Path) -> str:
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


def candidate_frame(inputs: AnalysisInputs) -> pd.DataFrame:
    deep = {r.trajectory_id for r in inputs.stage_c2}
    steps = inputs.stats.set_index("trajectory_id")["steps"]
    rows = []
    for result in inputs.stage_c2:
        for c in result.output.jump_in_candidates:
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
        for candidate in shallow.output.jump_in_candidates:
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


def jump_in_section(inputs: AnalysisInputs, figures: Path) -> str:
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


CLAUDE = re.compile(r"claude|opus|sonnet|haiku", re.IGNORECASE)


def limitations_section(inputs: AnalysisInputs) -> str:
    meta = inputs.meta
    authors = meta.history_author.fillna("")
    claude_share = authors.str.contains(CLAUDE).mean() if len(meta) else 0.0
    unknown_share = (authors == "").mean() if len(meta) else 0.0
    real = meta[meta.real_user]
    real_claude = real.history_author.fillna("").str.contains(CLAUDE).mean() if len(real) else 0.0
    lines = [
        "## 8. Limitations",
        "",
        f"- History authors: {100 * claude_share:.0f}% of ingested trajectories were written by "
        f"Claude models ({100 * real_claude:.0f}% within real-user sources), and "
        f"{100 * unknown_share:.0f}% have no recorded author. Later phases must report results by "
        "history author.",
        f"- Stage C2 read {len(inputs.stage_c2):,} trajectories, and the final lists are drawn only "
        "from those.",
        f"- Token counts are character-based estimates ({CHARS_PER_TOKEN} characters per token, "
        "calibrated against Haiku's own counts), not tokenizer counts.",
        "- Pattern tags see only action text, so user_report and effort_shortcut are under-tagged at Stage A.",
        "- Base commits for SWE-chat Claude Code sessions come from the first commit linked to the "
        "session's checkpoint (the session started from its parent); for DataClaw and the "
        "remaining SWE-chat sessions they are inferred as the last default-branch commit before "
        "the session started, marked in meta.base_commit_method.",
    ]
    return "\n".join(lines)
