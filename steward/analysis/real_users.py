"""Report section 4: real-user behavior, interruptions, pushback and persona labels."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from steward.analysis.figures import SERIES, horizontal_bars, save
from steward.analysis.inputs import AnalysisInputs
from steward.analysis.tables import markdown_table


def section(inputs: AnalysisInputs, figures: Path) -> str:
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
