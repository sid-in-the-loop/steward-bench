"""Report section 8: limitations backed by numbers."""

from __future__ import annotations

import re

from steward.analysis.inputs import AnalysisInputs
from steward.sources.registry import UNAVAILABLE

CLAUDE = re.compile(r"claude|opus|sonnet|haiku", re.IGNORECASE)


def section(inputs: AnalysisInputs) -> str:
    meta = inputs.meta
    authors = meta.history_author.fillna("")
    claude_share = authors.str.contains(CLAUDE).mean() if len(meta) else 0.0
    unknown_share = (authors == "").mean() if len(meta) else 0.0
    real = meta[meta.real_user]
    real_claude = real.history_author.fillna("").str.contains(CLAUDE).mean() if len(real) else 0.0
    by_domain = meta.groupby("domain").size()
    thin = [str(d) for d, n in by_domain.items() if n < 150]
    missing = ", ".join(sorted(UNAVAILABLE)) or "none"
    lines = [
        "## 8. Limitations",
        "",
        f"- Missing sources: {missing}. Reasons and what would unblock each are in SOURCES_STATUS.md.",
        f"- History authors: {100 * claude_share:.0f}% of ingested trajectories were written by "
        f"Claude models ({100 * real_claude:.0f}% within real-user sources), and "
        f"{100 * unknown_share:.0f}% have no recorded author. Later phases must report results by "
        "history author.",
        f"- Thin domains (fewer than 150 ingested trajectories): {', '.join(thin) or 'none'}.",
        f"- Stage C2 covers {len(inputs.stage_c2):,} trajectories; every other ranked row falls back "
        "to C1 scores and has no jump_in_phase, buttons, checkability or right_behavior_agreement.",
        "- Stage B and C1 used Claude Haiku 5.5 through the OpenRouter Batch API instead of the "
        "Anthropic Batch API named in the brief; model and prompts are unchanged.",
        "- Token counts are character-based estimates (3.2 characters per token), not tokenizer counts.",
        "- Pattern tags see only action text, so user_report and effort_shortcut are under-tagged at Stage A.",
        "- Base commits for SWE-chat Claude Code sessions come from the first commit linked to the "
        "session's checkpoint; the session started from that commit's parent.",
    ]
    return "\n".join(lines)
