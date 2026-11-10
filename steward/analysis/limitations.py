"""Report section 8: limitations backed by numbers."""

from __future__ import annotations

import re

from steward.analysis.inputs import AnalysisInputs
from steward.filter.render import CHARS_PER_TOKEN

CLAUDE = re.compile(r"claude|opus|sonnet|haiku", re.IGNORECASE)


def section(inputs: AnalysisInputs) -> str:
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
