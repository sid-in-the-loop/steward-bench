"""reports/PILOT.md: pilot token counts, cost per trajectory, projection and C1 to C2 agreement."""

from __future__ import annotations

import json

import pandas as pd
from scipy.stats import spearmanr

from steward.analysis.tables import markdown_table
from steward.config import Settings
from steward.pilot import PilotNumbers
from steward.schema import CRITERIA, CostRecord, StageC1Result, StageC2Result
from steward.store import read_jsonl


def agreement(c1: list[StageC1Result], c2: list[StageC2Result]) -> pd.DataFrame:
    c1_by_id = {r.trajectory_id: r for r in c1 if r.output is not None}
    rows = []
    for deep in c2:
        shallow = c1_by_id.get(deep.trajectory_id)
        if shallow is None or shallow.output is None:
            continue
        for criterion in CRITERIA:
            rows.append(
                {
                    "criterion": criterion,
                    "c1": shallow.output.scores[criterion].score,
                    "c2": deep.output.scores[criterion].score,
                }
            )
    return pd.DataFrame(rows, columns=["criterion", "c1", "c2"])


def agreement_table(pairs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for criterion, group in pairs.groupby("criterion", sort=False):
        varied = group.c1.nunique() > 1 and group.c2.nunique() > 1
        rows.append(
            {
                "criterion": criterion,
                "pairs": len(group),
                "exact agreement": f"{100 * (group.c1 == group.c2).mean():.0f}%",
                "within one": f"{100 * ((group.c1 - group.c2).abs() <= 1).mean():.0f}%",
                "mean C1": group.c1.mean(),
                "mean C2": group.c2.mean(),
                "spearman": spearmanr(group.c1, group.c2).statistic if varied else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def actual_spend_lines(settings: Settings) -> list[str]:
    """Spend per stage from data/costs.jsonl, including abandoned pilot attempts."""
    records = read_jsonl(settings.paths.costs, CostRecord)
    if not records:
        return ["No batches recorded yet."]
    frame = pd.DataFrame([r.model_dump() for r in records])
    by_stage = (
        frame.groupby("stage")
        .agg(
            batches=("batch_id", "count"),
            requests=("requests", "sum"),
            prompt_tokens=("prompt_tokens", "sum"),
            completion_tokens=("completion_tokens", "sum"),
            cost_usd=("cost_usd", "sum"),
        )
        .reset_index()
    )
    total = float(frame.cost_usd.sum())
    return [
        markdown_table(by_stage),
        "",
        f"Total spend on Stages B and C1, pilots included: ${total:.2f} of the "
        f"${settings.caps.stage_b_c1_usd:.2f} cap.",
    ]


def write_pilot_report(settings: Settings) -> str:
    root = settings.paths.pilot
    numbers = PilotNumbers.model_validate(json.loads((root / "numbers.json").read_text()))
    c1 = read_jsonl(root / "stage_c1.jsonl", StageC1Result)
    c2 = read_jsonl(root / "stage_c2.jsonl", StageC2Result)
    cap = settings.caps.stage_b_c1_usd
    stages = pd.DataFrame(
        [
            {
                "stage": name,
                "requests": n.requests,
                "valid outputs": n.valid,
                "prompt tokens": n.prompt_tokens,
                "completion tokens": n.completion_tokens,
                "prompt tokens per trajectory": n.prompt_tokens_per_trajectory,
                "cost (USD)": f"{n.cost_usd:.4f}",
                "cost per trajectory (USD)": f"{n.cost_per_trajectory:.5f}",
            }
            for name, n in (("B", numbers.b), ("C1", numbers.c1))
        ]
    )
    lines = [
        "# Pilot",
        "",
        f"{numbers.trajectories} Stage A survivors, picked round-robin across domains "
        f"({', '.join(f'{d}: {n}' for d, n in sorted(numbers.domains.items()))}), went through "
        "Stage B and Stage C1 on the OpenRouter Batch API with anthropic/claude-haiku-5.5. "
        + (
            f"{len(c2)} of them have Stage C2 results from cloud sessions. "
            if c2
            else "Their Stage C2 cloud sessions returned no results (reports/BLOCKERS.md). "
        )
        + "Token counts and costs are the usage the batches reported. The first pilot attempt "
        "(pilot_b's first batch and pilot_c1_attempt1) ran with default reasoning and too small an "
        "output budget; it is kept in the spend table and excluded from the per-trajectory "
        "numbers.",
        "",
        markdown_table(stages),
        "",
        f"Stage B answered YES for {numbers.b_yes} of {numbers.trajectories}.",
        "",
        "## Projection for the full run",
        "",
        f"- Stage B: {numbers.full_b_inputs:,} inputs at the pilot cost per trajectory, about "
        f"${numbers.projected_b_usd:.2f}.",
        f"- Stage C1: {numbers.full_c1_inputs:,} inputs (the pilot YES rate applied to the B "
        f"inputs, capped at {settings.stage_c1.top_n:,}), about ${numbers.projected_c1_usd:.2f}.",
        f"- Total: about ${numbers.projected_total_usd:.2f} against the ${cap:.2f} cap, so the "
        + ("full run fits." if numbers.projected_total_usd <= cap else "full run does not fit."),
        "",
        "## Actual spend",
        "",
        *actual_spend_lines(settings),
        "",
        "## C1 to C2 agreement",
        "",
    ]
    pairs = agreement(c1, c2)
    if pairs.empty:
        lines.append("No pilot C2 results are available yet.")
    else:
        rho = spearmanr(pairs.c1, pairs.c2).statistic
        lines += [
            f"{len(c2)} trajectories have both C1 and C2 results. Across all criteria, scores agree "
            f"exactly on {100 * (pairs.c1 == pairs.c2).mean():.0f}% of pairs and within one point "
            f"on {100 * ((pairs.c1 - pairs.c2).abs() <= 1).mean():.0f}%; Spearman correlation is "
            f"{rho:.2f}.",
            "",
            markdown_table(agreement_table(pairs)),
        ]
    text = "\n".join(lines) + "\n"
    path = settings.paths.reports / "PILOT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text
