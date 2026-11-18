# Steward Bench

Steward Bench measures what an AI agent does when it takes over routine work from a previous
agent and the work has changed since the handover notes were written. Following the notes
as written would destroy a colleague's work. The question is whether the agent protects
that work and still finishes its own task.

Each case is one routine job in a real environment: a git repository, an operations
checkout, a workplace of mail and calendars, a machine-learning lab, or a Postgres database.
The agent runs real commands, other people answer when asked, and harm is read from the
state the environment is left in, never from what the agent says.

## The benchmark at a glance

A session runs one case as a series of loops in one conversation and one environment. In
each loop the requester hands over the routine task again and the agent works until it
reports. On a drift loop the owner has changed something before the handover, so the
routine as the notes describe it causes harm; on a control loop nothing has changed and the
routine is correct. Nothing changes while the agent is working.

| Level | Name | What varies between sessions |
|---|---|---|
| I | Single-factor sessions | One setting at a time from a baseline: length, users present, number and position of drift loops, handover notes, how often users speak |
| II | Seeded drift schedules | Which loops drift, drawn from a seed |
| III | Varied users | Each user's presence, rate of speaking, share of unrelated talk, belief and stance, drawn from a seed |
| IV | Multiple drifts | A second person's change on the same, a related or an unrelated object, or a change left in place over later loops |
| V | User-made drifts | The owner's change is made by a simulated owner, recorded once and replayed for every model |

The cases cover five domains with five cases each:

| Domain | Environment | Example routine and drift |
|---|---|---|
| Software | A git repository with a remote, tests and a stand-in for `gh` | Rebuild an integration branch that a teammate has pushed a fix to |
| Terminal | An operations checkout with small tools over files | Apply retention to a partition whose backup no longer holds every row |
| Workplace | Mail, calendar, sheets, forms and Canvas tools over a recorded world | Recreate meeting events that a colleague has already sent invitations for |
| ML operations | A shared lab checkout with checkpoints, a queue and a registry | Keep the last three checkpoints of a run whose older checkpoint is now a baseline |
| Database operations | A Postgres server that stays up for the session | Apply a migration to a table a colleague's backfill is holding |

The tested models are `gpt-6-luna`, `gpt-5.6-luna` and `claude-haiku-5-5`. Claude Haiku 5.5
also writes the users' reworded lines, answers a user's follow-up questions and plays the
simulated owner; everything else users say is fixed text in the case.

## Quickstart

```bash
uv sync                                  # Python 3.12, dependencies pinned in uv.lock
cp .env.example .env                     # then set OPENAI_API_KEY and ANTHROPIC_API_KEY
git config core.hooksPath .githooks      # run scripts/check before every commit and push
uv run pytest                            # every test runs on fixtures, without containers or models
uv run steward case-check                # what every case provides at each level
uv run steward bench-run                 # the full plan and its cost; makes no model calls
```

Running a case needs Apptainer and the images under `data/apptainer/` (`workspace.sif`,
`postgres.sif`, and for workplace cases `gym.sif`).

## Running the benchmark

```bash
uv run steward session-build              # build every case and run its references
uv run steward bench-run --pilot --confirm            # one setting per domain and level
uv run steward bench-run --confirm --max-usd 180      # everything not yet stored
uv run steward bench-status                           # planned, stored and complete sessions
uv run steward bench-view --out view                  # the session viewer
uv run steward bench-digest --out data/cases/analysis/digests  # sessions as text, measures.csv
uv run steward bench-set-aside                        # set aside sessions a runner fault made wrong
uv run steward bench-sample                           # sessions to read to check the scorer's labels
uv run steward bench-factors                          # how each setting factor moves harm
uv run steward bench-check-gap                        # harm by how stale the agent's last look was
uv run steward bench-discrimination                   # flags on drift loops against loops without drift
uv run steward deck-data                              # the numbers the presentation page shows
```

`bench-run` prints a plan priced from the stored sessions of each model before it makes any
call, refuses a full run until a pilot has been stored, runs the three models at once under
one limit in dollars, and appends each finished session to
`data/cases/sessions/<model>.jsonl`, so a stopped run resumes where it was. docs/levels.md
describes the levels, the users, the message bank, the recorded owners and the viewer.
docs/run_plan.md lists exactly what a full run contains. docs/analysis.md describes the
analysis of the stored sessions: the label check, what moves harm, stale checks and whether
a model tells drift apart or is cautious everywhere.

## The presentation

`presentation/` is a single web page that presents the benchmark and its findings: why
handovers matter, what a shift is, routine and changed shifts, what a session varies, one
ML operations session played out, the findings, the plan for scaling, and a link to the
session viewer. It is published as a claude.ai artifact. Every number on it comes from
`steward deck-data`, which reads the measures table of `bench-digest` and the rates of
`bench-check-gap` and writes `presentation/numbers/deck.js`; nothing is typed in by hand.
docs/presentation.md describes the page, how to rebuild it and what each figure shows.

## Repository layout

```
cases/
  _bases/                 one base script per environment: tally.sh, ops.sh, lab.sh, warehouse_db.sh
  <domain>-<hash>/        one folder per case, named by its domain and a hash of its title
steward/
  cases/                  the benchmark: case model, session loop, levels, bank, recorder, runner, viewer
  execution/              model policies for the OpenAI and Anthropic APIs, retries, usage and cost
  environment/            containers, the workplace worlds and their tool servers, the gh stand-in
  cli.py                  the single entry point, `steward <command>`
  config.py               paths and settings
tests/cases/              tests of the benchmark on fake environments
presentation/             the presentation page: index.html, the session scene, assets/, numbers/deck.js
docs/                     cases.md, levels.md, adding_a_case.md, run_plan.md, analysis.md, presentation.md
data/                     built environments and stored sessions; not in git
```

| Module in steward/cases | Responsibility |
|---|---|
| `case.py` | The case model, its identifier and its version |
| `sessions.py` | Settings, the session loop, per-loop measures |
| `levels.py`, `level_settings.py` | Users as parameters, the handover plan, the message bank, the settings of each level |
| `bank.py` | Writing and checking a case's message bank |
| `recorder.py` | Recording a simulated owner for Level V |
| `bench.py`, `session_commands.py` | Planning, pricing, running and checking sessions under a limit |
| `bench_view.py` | The session viewer |
| `catalogue.py`, `digest.py` | Every stored session with its status; sessions as text and a per-loop table |
| `validation_sample.py` | A stratified sample of sessions for checking the scorer's labels |
| `factors.py` | How each setting factor moves harm, with case-clustered intervals |
| `check_gap.py` | Harm on drift loops by how long before acting the agent last looked |
| `discrimination.py` | Flags raised on drift loops against loops without drift |
| `deck_data.py` | The numbers the presentation page shows |
| `authoring.py` | Starting a new case and reporting what a case provides |
| `postgres.py`, `sandbox.py`, `run.py` | Building environments and running commands in them |

## Adding a case

docs/adding_a_case.md is the guide: what makes a good case, which domain and base it
belongs to, and the commands that take it from a copy of a working case to checked, with
its message bank and recorded owners. In short:

```bash
uv run steward case-new --domain mlops --title "Archiving runs that a paper still cites"
uv run steward case-check --case <id> --build
uv run steward bank-build --case <id> --confirm
uv run steward owner-record --case <id> --confirm
```

## Costs

Every command that calls a model prints an estimate and stops unless `--confirm` is passed;
`bench-run` also stops starting sessions at `--max-usd`. In the sessions stored so far a
session of sixteen loops cost about $0.01 on `gpt-6-luna`, $0.013 on `gpt-5.6-luna`, and
between $0.015 and $0.04 on `claude-haiku-5-5`. Writing a case's message bank costs under a
cent, a recorded owner about two cents, and the exchanges between users, written once
with Claude Sonnet 5.5, about $8 for all teams. The full run
of docs/run_plan.md is priced at about $176, within the limit of about $170 the user set
for it on 2026-11-16.

## Limitations

- Claude Haiku 5.5 is both a tested model and the model that writes and answers for the
  users, so in its own sessions it partly talks to itself. Results for it should say so.
- The routine and its drift are written for the benchmark; the environments are real but
  small, and the histories are scripted and executed rather than recorded from users.
- A user's follow-up answers are written by a model and can vary between runs of the same
  setting; their first answers and everything they say unprompted are fixed text on disk.
- The database backfill case has no recorded owner, because a recorded command cannot hold
  a database session open.
- A case's checks read state, so harm the checks were not written for goes uncounted.

## Earlier work in this repository

Before the cases, the project collected agent trajectories from public sources and built
scenarios from them. That pipeline is still here: docs/pipeline.md and docs/sources.md for
trajectory collection and filtering (reports/PHASE1.md), docs/phase2_design.md and
docs/scenarios.md for the scenarios (reports/PHASE2.md). Its commands are listed by
`steward --help`.
