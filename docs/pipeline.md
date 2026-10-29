# Running the pipeline

This is every stage of Phase 1 in order, with the command, its input, its output and how to check
it. Every stage reads the previous stage's output, skips work that is already done, and logs
counts in and out to data/logs/steward.log. Every stage can be re-run safely.

Commands load `.env` from the repository root. OPENROUTER_API_KEY is needed for Stages B and C1,
and HF_TOKEN for gated downloads.

## 0. Download and ingest

```bash
uv run steward sources                 # list sources and whether each has a loader
uv run steward download <source>       # or: all
uv run steward ingest <source>         # or: all; --force rewrites a source
```

Output: data/normalized/<source>/meta.parquet, with one row per trajectory, and steps.jsonl.gz (gzip-compressed JSONL), with
one line per step. A `_DONE` marker is written last, so an interrupted ingest leaves no partial
source. Mixed real-user sources get their domain from steward/sources/domain_rule.py. Sessions
the rule cannot decide are marked `domain_decided_by_rule = false` and take the domain Stage B
returns.

Check: `uv run steward validate normalized data/normalized/<source>`.

For real-user sources whose sessions name a public GitHub repository but no commit (SWE-chat,
Pi, DataClaw), run `uv run steward infer-commits <source>` after ingesting. For each session with
at least 40 actions, it takes the last default-branch commit before the session started, from a
bare treeless clone in data/raw/_git, and marks it in meta as `base_commit_method`.

## 1. Stage A: rules, tags, prescore

```bash
uv run steward stage-a                 # --force recomputes every source
```

This runs checks A1 to A5 with the thresholds in steward/config.py (StageAThresholds), the
moment-kind patterns in steward/filter/tags.py, and the prescore from brief 6.1. No model calls.
Output: data/stage_a/<source>.parquet, with one row per trajectory: passed, failed_check, counts,
tags with step indices, and prescore.

## 2. Pilot

Run the pilot before any full model-calling run.

```bash
uv run steward pilot                   # prints the estimate and stops
uv run steward pilot --confirm --max-usd 0.5
uv run steward c2-handoff --pilot      # one shard for the C2 pilot
```

The pilot picks 40 Stage A survivors round-robin across domains and runs them through B and C1.
It writes data/pilot/numbers.json, with token counts, cost per trajectory and the projected cost
of the full B and C1 runs. Copy those numbers into reports/PILOT.md.

## 3. Stage B: triage

```bash
uv run steward stage-b                 # estimate only
uv run steward stage-b --confirm --max-usd 3
```

Input: the top 8,000 Stage A survivors by prescore, with at least 600 per domain where
available, plus every survivor in terminal_devops and workplace (`StageBConfig.full_domains`).
Each request is a summary of about 2,500 tokens (steward/filter/render.py
triage_summary) under prompts/stage_b.md. Output: data/stage_b/results.jsonl.

## 4. Stage C1: criteria scoring

```bash
uv run steward stage-c1
uv run steward stage-c1 --confirm --max-usd 8
```

Input: the top 1,500 Stage B passes, with a floor of 150 per domain, plus every pass in
terminal_devops and workplace (`StageC1Config.full_domains`). Each request is the windowed
rendering from brief 6.4, capped at 40,000 tokens, under prompts/stage_c1.md. Output:
data/stage_c1/results.jsonl.

### Cost guard and batches

Every model-calling command estimates the whole job first, at 3.2 characters per token and the
batch list price, and prints it. It refuses to submit without `--confirm`, refuses if the
estimate exceeds `--max-usd`, and refuses if the estimate plus recorded spend would pass the
combined B and C1 cap (steward/config.py, Caps). Requests above 99k prompt tokens are refused.

Batches go to the OpenRouter Batch API (POST /api/v1/batches) with the model
anthropic/claude-haiku-5.5. Each submitted batch is appended to
data/batches/<stage>/manifest.jsonl before polling. A re-run polls the existing batches instead
of resubmitting them. Results are saved to data/batches/<stage>/<batch_id>.results.jsonl, and the
spend each batch reports is appended to data/costs.jsonl.

## 5. Stage C2: deep read in cloud sessions

```bash
uv run steward c2-handoff              # data/handoff/c2/shard_NNN.jsonl
```

The handoff takes up to 180 distinct tasks per domain for swe, terminal_devops and workplace
(`StageC2Config`), ranked by the sum of the two highest C1 axis scores. A task is the
trajectory's task environment, else its repository and commit; only the best trajectory of each
task is sent, so the pool is not filled with the same task run by several models.

The remaining steps are cloud sessions and branch handling, described in docs/stage_c2_cloud.md.
After merging, the output is data/stage_c2/results.jsonl.

## 6. Ranking

```bash
uv run steward rank
```

This writes data/ranked/<domain>/<axis>.csv for every domain with ingested data and every one of
the five axes. Scores come from C2, falling back to C1. Rows are sorted by axis score, then C2
right_behavior_agreement, then agent actions. Trajectories whose candidates all have unsafe
precedent are excluded, and so are trajectories with an axis score of 0. Each list keeps the top
40.

It also writes data/ranked/<domain>/top_120.csv for swe, terminal_devops and workplace: the best
120 distinct tasks of the domain. C2-scored trajectories come first, ordered by the sum of the two
highest axis scores, then C2 right_behavior_agreement, then agent actions. Trajectories with all
axis scores at 0, or whose candidates all have unsafe precedent, are excluded.

## 7. Analysis and status reports

```bash
uv run steward sources-status          # reports/SOURCES_STATUS.md
uv run steward analyze                 # reports/PHASE1_REPORT.md and reports/figures/
```

## Validation

```bash
uv run steward validate-all            # every stage output that exists
uv run steward validate <stage> <path> # one file; stages: normalized a b c1 c2 c2_input c2_merged ranked final
```
