"""Report sections 1 to 4, about the corpus: the funnel per source and domain, trajectory shape,
moment kinds, and real-user behavior.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from steward.analysis.inputs import AnalysisInputs
from steward.analysis.plotting import (
    SERIES,
    heatmap,
    horizontal_bars,
    markdown_table,
    percent,
    quantiles,
    save,
)
from steward.filter.render import CHARS_PER_TOKEN
from steward.schema import MOMENT_KINDS

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


def funnel_section(inputs: AnalysisInputs, ranked_ids: set[str], figures: Path) -> str:
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


def corpus_section(inputs: AnalysisInputs, figures: Path) -> str:
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
        f"from characters ({CHARS_PER_TOKEN} characters per token); duration is the span between the first and "
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


def moments_section(inputs: AnalysisInputs, figures: Path) -> str:
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


def real_users_section(inputs: AnalysisInputs, figures: Path) -> str:
    meta = inputs.meta[inputs.meta.real_user]
    if meta.empty:
        return "## 4. Real-user behavior\n\nNo real-user sources were ingested."
    events = inputs.interruptions.merge(meta[["trajectory_id", "source"]], on="trajectory_id")
    steps = inputs.stats.set_index("trajectory_id")["steps"]
    per_source = []
    for source, group in meta.groupby("source"):
        source_events = events[events.source == source]
        turns = int(group.user_messages.sum())
        per_source.append(
            {
                "source": source,
                "sessions": len(group),
                "user turns": turns,
                "interruptions": len(source_events),
                "interruptions per user turn": len(source_events) / turns if turns else 0.0,
                "sessions with an interruption": source_events.trajectory_id.nunique(),
            }
        )
    rates = pd.DataFrame(per_source)

    relative = (events.step / events.trajectory_id.map(steps)).dropna()
    figure, axis = plt.subplots(figsize=(6.5, 2.6))
    axis.hist(relative, bins=20, range=(0, 1), color=SERIES[0], rwidth=0.9)
    axis.set_xlabel("position of interruption within session (0 start, 1 end)")
    axis.set_ylabel("interruptions")
    axis.set_title("Where real-user interruptions fall", loc="left")
    position_png = save(figure, figures, "interruption_position")

    preceding = events.prev_tool.replace("", "none").value_counts().head(12)
    preceding_png = save(
        horizontal_bars(
            list(preceding.index),
            preceding.tolist(),
            "Action before each interruption (tool)",
            "interruptions",
        ),
        figures,
        "interruption_preceding_tool",
    )
    commands = events[
        events.prev_tool.str.lower().isin(["bash", "exec_command", "shell", "run_terminal_cmd"])
    ]
    top_commands = commands.prev_command.value_counts().head(10)

    labels = []
    for row in meta.itertuples(index=False):
        values = json.loads(str(row.meta_json))
        labels.append(
            {
                "source": row.source,
                "persona": values.get("user_persona"),
                "success": values.get("session_success"),
            }
        )
    label_frame = pd.DataFrame(labels)
    personas = (
        label_frame.dropna(subset=["persona"])
        .groupby(["source", "persona"])
        .size()
        .rename("sessions")
        .reset_index()
    )

    lines = [
        "## 4. Real-user behavior",
        "",
        f"Across real-user sources there are {len(events):,} interruptions over "
        f"{int(meta.user_messages.sum()):,} user turns. Interruptions are user or system steps "
        "matching `[Request interrupted by user]`, Codex `turn_aborted` events with reason "
        "interrupted, and aborted assistant turns in Pi sessions.",
        "",
        markdown_table(rates),
        "",
        f"![Interruption position]({position_png})",
        "",
        f"![Action before interruption]({preceding_png})",
        "",
        "First word of the shell command before an interruption, when the preceding action was a shell call:",
        "",
        markdown_table(top_commands.rename_axis("command").reset_index(name="interruptions"))
        if len(top_commands)
        else "None.",
        "",
        "SWE-chat persona labels (LLM-assigned in the source dataset):",
        "",
        markdown_table(personas) if not personas.empty else "No persona labels.",
        "",
        "SWE-chat prompt_pushback and prompt_intent labels live in the 6.4 GB conversations "
        "table, which was not downloaded in this run; pushback is therefore not reported here.",
    ]
    return "\n".join(lines)
