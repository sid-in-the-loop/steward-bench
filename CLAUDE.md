# CLAUDE.md

Read PROJECT_BRIEF.md first. It defines the scope, stages, outputs and stop condition for
Steward Bench Phase 1.

## Goal

Collect agent trajectories from every source, normalize them, filter them in stages, rank
them per domain and axis, and analyze them. Stop when the stop condition in section 2.3 of the
brief is met, post a short summary, and wait. Do not start Phase 2.

## Code

- Readable over clever. Small modules with one responsibility and clear names.
- Type hints everywhere. Pydantic models for data. No untyped dicts across module boundaries.
- Comment only where the reason is not obvious from the code. No narration, commented-out
  code, TODO notes or decorative banners.
- One config module and one CLI entry point, `steward <command>`. No hard-coded paths.
- Every stage is resumable and deterministic.
- Tests for every loader, pattern, renderer, stage and validator, using small fixtures.
- `ruff check`, `ruff format --check`, `mypy` and `pytest` pass before every commit.
- Dependencies managed with uv and pinned.

## Documentation

- README.md is production grade: purpose, architecture, quickstart, running each stage,
  configuration, outputs, adding a source, costs, limitations.
- docs/pipeline.md, docs/sources.md and docs/stage_c2_cloud.md as described in the brief.
- Docs change in the same commit as the code they describe.
- Plain prose. Use headings, tables and lists only where they aid scanning. No emoji, no
  decorative separators, no bold scattered through sentences.

## Naming

- Never invent names, titles or slugs for scenarios, tasks or results. Identifiers are
  deterministic: trajectory_id is `{source}:{native_id}`, candidates are
  `{trajectory_id}#t{step}`.
- Modules, functions and files are named for what they do.

## Data

- Never print raw trajectories, transcripts or large files into the conversation. Inspect only
  counts, schemas, the first 20 lines of outputs, and error logs.
- Run downloads and long stages in the background and follow progress through log files.
- data/ is ignored by git. Only data/handoff on handoff/* branches and data/results on
  results/* branches are committed, and every committed file stays under 20 MB.
- Read secrets from environment variables only. Never commit them.
- If a source cannot be obtained, record it in reports/SOURCES_STATUS.md with the reason and
  continue.

## Spend

- Stages B and C1 use the Batch API with claude-haiku-5-5, capped at $10 combined.
- Stage C2 runs in cloud sessions started with `claude --cloud`, as described in section 3.2
  of the brief. Do not call Opus or Sonnet through the API from this machine.
- Every model-calling command estimates cost first and requires --confirm. Run the
  40-trajectory pilot and report its numbers before any full run.
- Ask before exceeding a cap, deleting downloaded data, or changing thresholds in the brief.

## Git

- Small commits with clear imperative messages that say what changed and why. One commit
  per logical change; code, its tests and its docs go together.
- Commit when a piece of work is finished and the gates pass, and push straight after, so
  origin never lags far behind. Never leave a day's work uncommitted.
- `scripts/check` runs the gates: no data/ or secrets files and nothing over 20 MB staged,
  then `ruff check`, `ruff format --check`, `mypy` and `pytest`. The hooks in `.githooks`
  run it before every commit and push; enable them once per clone with
  `git config core.hooksPath .githooks`. Never bypass them with `--no-verify`; fix the cause.
- Stage files by name, never `git add -A`, and read `git status` before committing.
- main holds code and docs only. Data handoff uses handoff/c2; cloud results use
  results/c2-shard-NNN.

## Decisions

- Decide implementation details, libraries and file layout within the brief yourself.
- Ask before changing scope, thresholds, caps or criteria.
