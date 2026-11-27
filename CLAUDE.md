# CLAUDE.md

Steward Bench is the benchmark described in README.md and docs/framework.md: 120 tasks, the
session harness that runs them, and the scoring of stored sessions.

## Code

- Readable over clever. Small modules with one responsibility and clear names.
- Type hints everywhere. Pydantic models for data. No untyped dicts across module boundaries.
- Comment only where the reason is not obvious from the code. No narration, commented-out
  code, TODO notes or decorative banners.
- One config module and one CLI entry point, `steward <command>`. No hard-coded paths.
- Every command is resumable and deterministic.
- Tests for every loader, check, scorer and harness path, using small fixtures.
- `ruff check`, `ruff format --check`, `mypy` and `pytest` pass before every commit.
- Dependencies managed with uv and pinned.

## Documentation

- README.md is production grade: purpose, requirements, setup, commands, outputs, costs,
  limitations.
- docs/framework.md holds the vocabulary; docs/tasks.md, docs/running.md and docs/scoring.md
  describe the tasks, the runs and the scoring.
- Docs change in the same commit as the code they describe.
- Plain prose. Use headings, tables and lists only where they aid scanning. No emoji, no
  decorative separators, no bold scattered through sentences.

## Naming

- Never invent names, titles or slugs for tasks or results. Identifiers are deterministic: a
  task is `{domain}-{hash of its title}`, a trajectory `{source}:{native_id}`.
- Modules, functions and files are named for what they do.
- The benchmark's vocabulary is fixed in docs/framework.md. Use those terms and no others in
  answers, docs, tables and plots. Never coin a new term or a synonym for one of them; if a
  concept seems to lack a name, ask before naming it.

## Data

- Never print raw trajectories, transcripts or large files into the conversation. Inspect only
  counts, schemas, the first 20 lines of outputs, and error logs.
- Run long commands in the background and follow progress through log files.
- data/ is ignored by git, and every committed file stays under 20 MB.
- Read secrets from environment variables only. Never commit them.

## Spend

- Do not call Opus through the API from this machine. claude-sonnet-5-5 is called only as the
  user simulator (user, 2026-11-15).
- Every model-calling command estimates cost first and requires --confirm. Run a pilot and
  report its numbers before any full run.
- Ask before exceeding a cap, deleting downloaded data, or changing thresholds.

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
- main holds code and docs only.

## Decisions

- Decide implementation details, libraries and file layout yourself.
- Ask before changing scope, thresholds, caps, scoring or criteria.
