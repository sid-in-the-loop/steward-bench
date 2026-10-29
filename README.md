# Steward Bench, Phase 1: trajectory collection and filtering

Steward Bench measures whether AI agents act as good stewards when they take over someone else's
long-running session part way through. Later phases rebuild the environment at a step t of a
real recorded trajectory, hand the history up to t to a new model, change the situation at t,
and score the continuation with deterministic checks.

Phase 1, this repository, finds the material for those scenarios. It collects agent
trajectories from public sources, normalizes them into one format, filters them in four stages,
ranks the survivors per domain and per safety axis, and analyzes the corpus. It does not build
scenarios, run agents or score models.

The full specification is the Phase 1 brief, in project_desc/traj_collection.md, which is kept
outside version control. The engineering rules are in CLAUDE.md.

## Architecture

```
sources ──download──> data/raw/<source>/
        ──ingest────> data/normalized/<source>/{meta.parquet, steps.jsonl.gz}  common format
        infer-commits  base commits for public-repo sessions without one
Stage A ────────────> data/stage_a/<source>.parquet      rules A1 to A5, moment tags, prescore (free)
Stage B ────────────> data/stage_b/results.jsonl         triage, Haiku 5.5 on a batch API
Stage C1 ───────────> data/stage_c1/results.jsonl        eight criteria scored 0 to 3, Haiku 5.5
Stage C2 ───────────> data/stage_c2/results.jsonl        deep read, opus in cloud sessions
rank ───────────────> data/ranked/<domain>/<axis>.csv    top 40 per domain and axis
                       data/ranked/<domain>/top_120.csv   best 120 distinct tasks per domain
analyze ────────────> reports/PHASE1_REPORT.md, reports/figures/
```

| Module | Responsibility |
|---|---|
| steward/schema.py | Pydantic models: Step, Trajectory and every stage result |
| steward/config.py | Paths, thresholds, caps and model settings in one place |
| steward/store.py | Reading and writing normalized sources and JSONL outputs |
| steward/sources/ | One loader per source, shared transcript parsers, the domain rule, the registry |
| steward/filter/tags.py | Moment-kind regex patterns (brief Appendix D, extended) |
| steward/filter/stage_a.py | Checks A1 to A5, tags and prescore |
| steward/filter/render.py | Stage B summaries and the windowed rendering for C1 and C2 |
| steward/filter/batch.py | Batch API client: estimate, cost guard, submit, poll, collect, spend log |
| steward/filter/stage_b.py, stage_c1.py | Input selection, requests and result parsing |
| steward/filter/c2_handoff.py | C2 selection, shards, per-trajectory split and result merge |
| steward/filter/redact.py | Secret redaction for text that leaves the cluster |
| steward/filter/validate.py | Schema validation for every stage output |
| steward/filter/rank.py | Per-domain, per-axis ranking |
| steward/analysis/ | One module per report section, plus figures and the sources status |
| steward/cli.py | The single entry point, `steward <command>` |
| prompts/ | The Stage B, C1 and C2 prompts, verbatim from the brief |

Later phases (fork runtime, model connectors, sandboxes, visualizer) will live beside these
modules. The extension points are the Trajectory format, which keeps history_author, harness,
repo, base_commit and task_env, and the ranked CSV columns, which keep the jump-in step, its
phase, buttons, checkability and unsafe-precedent flags.

## Quickstart

```bash
uv sync                                  # Python 3.12, dependencies pinned in uv.lock
cp .env.example .env                     # then fill in OPENROUTER_API_KEY and HF_TOKEN
uv run pytest                            # every test runs on synthetic fixtures
uv run steward sources                   # list sources
uv run steward download swe_chat && uv run steward ingest swe_chat
uv run steward stage-a
uv run steward pilot                     # prints the estimate
uv run steward pilot --confirm --max-usd 0.5
```

## Running each stage

docs/pipeline.md walks through every command in order, with inputs, outputs and checks.
docs/stage_c2_cloud.md is the exact procedure for the C2 cloud sessions and for the cluster side
of the handoff. In short:

```bash
uv run steward ingest all
uv run steward stage-a
uv run steward stage-b --confirm --max-usd 3
uv run steward stage-c1 --confirm --max-usd 8
uv run steward c2-handoff               # then cloud sessions, see docs/stage_c2_cloud.md
uv run steward c2-merge
uv run steward rank
uv run steward sources-status
uv run steward analyze
uv run steward validate-all
```

Each stage skips work that is already done, so an interrupted command can be re-run. Model
batches are recorded before polling, so a re-run resumes polling instead of resubmitting.

## Configuration

Settings live in steward/config.py as pydantic models with the brief's defaults. Change them
there, not in the stage code.

| Setting | Default | Where |
|---|---|---|
| Data directory | `./data`, or the STEWARD_DATA environment variable | `load_paths` |
| Stage A thresholds | 40 actions, 1 state change, 2 user messages, 30% agent share | `StageAThresholds` |
| Stage B input | top 8,000 by prescore, floor of 600 per domain, all of terminal_devops and workplace, 2,500-token summaries | `StageBConfig` |
| Stage C1 input | top 1,500 B passes, floor of 150 per domain, all of terminal_devops and workplace, 40,000-token renderings | `StageC1Config` |
| Windows | 25 steps before and 15 after, results trimmed to 400 + 400 characters | `RenderConfig` |
| Stage C2 | 180 distinct tasks per domain for swe, terminal_devops and workplace, 25 trajectories and 20 MB per shard, 6 sessions | `StageC2Config` |
| Model | anthropic/claude-haiku-5.5 on the OpenRouter Batch API | `ModelConfig` |
| Spend cap | $15 combined for B and C1 | `Caps` |

Secrets are read from environment variables only, loaded from `.env`, which git ignores:
OPENROUTER_API_KEY for Stages B and C1, and HF_TOKEN for gated Hugging Face datasets. Stage C2
runs in `claude --cloud` sessions under the owner's claude.ai login and needs no key.

## Outputs

| Output | Content |
|---|---|
| data/normalized/<source>/ | meta.parquet, one row per trajectory; steps.jsonl.gz, one line per step |
| data/stage_a/<source>.parquet | pass or fail, the failed check, counts, tags with step indices, prescore |
| data/stage_b/results.jsonl | decision, reason, domain, best steps, moment kinds, offline rebuild |
| data/stage_c1/results.jsonl | eight criterion scores with evidence, up to three jump-in candidates, flags |
| data/handoff/c2/shard_NNN.jsonl | C2 inputs: rendering plus the C1 result, on branch handoff/c2 |
| data/results/c2/shard_NNN.jsonl | C2 outputs, on branches results/c2-shard-NNN |
| data/stage_c2/results.jsonl | merged and validated C2 results |
| data/ranked/<domain>/<axis>.csv | top 40 per domain and axis, with the columns in brief section 8 |
| data/ranked/<domain>/top_120.csv | best 120 distinct tasks for swe, terminal_devops and workplace |
| data/costs.jsonl | actual spend per batch |
| reports/SOURCES_STATUS.md | every source, ingested or unavailable, with license and reason |
| reports/PILOT.md | pilot token counts, cost per trajectory and full-run projection |
| reports/PHASE1_REPORT.md | the corpus and selection analysis, with figures in reports/figures/ |
| reports/DECISIONS.md, BLOCKERS.md, STATUS.md | decisions made during the run, blockers, and final status |

data/ is ignored by git. Only data/handoff on handoff/* branches and data/results on results/*
branches are committed, and every committed file stays under 20 MB.

## Adding a source

docs/sources.md describes the loader interface, the format rules, the reusable parsers for
Claude Code, Codex, Pi, Copilot, OpenAI-style and ChatML transcripts, and the steps to register,
test and ingest a new source.

## Costs

Stages B and C1 are the only stages that spend money from this machine. Each runs through the
OpenRouter Batch API at half the sync price: $0.05 per million prompt tokens and $0.25 per
million completion tokens for Claude Haiku 5.5. Every request stays under 99k prompt tokens,
because the price is five times higher above 100k. At the brief's sizes, 8,000 summaries of
about 2,500 tokens cost about $1 for B, and 1,500 renderings of up to 40,000 tokens cost about $3
for C1. The pilot measures the real figures, which are in reports/PILOT.md, and data/costs.jsonl
records actual spend per batch. Stage C2 runs on cloud-session credits. Its size is bounded by
the shard count: at most 60 trajectories per domain, 25 per shard.

## Limitations

- Some sources are gated or unpublished, so they could not be obtained. Each one is listed in
  reports/SOURCES_STATUS.md with what would unblock it.
- Real-user material is dominated by sessions with Claude models. Results must be reported by
  history author.
- Pattern tags see action text only. Moment kinds such as user_report and effort_shortcut depend
  on the model stages.
- Token counts are character-based estimates.
- Stage B and C1 are single passes by a small model. C2 rereads 180 tasks per domain in swe,
  terminal_devops and workplace; everything else rests on C1.
- Section 8 of reports/PHASE1_REPORT.md lists the limitations measured on the actual corpus.
