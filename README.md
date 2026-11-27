# Steward Bench

Steward Bench measures whether an AI agent keeps its actions inside its mandate when it works
in a real system for a long time. The agent is given real access to an environment, and that
access stays the same while its permission changes: an earlier approval goes stale, the
principal revises the mandate, or a non-principal asks for something only the principal may
allow. The benchmark asks whether the agent notices and acts within what it is currently
allowed to do, and whether it still gets its ordinary work done.

docs/framework.md defines every term used here and in the results.

## What a run measures

There are 120 tasks. Each is one scenario in one environment, and a session is one agent
working one task for 12 shifts over 2 days. Most shifts are routine shifts; on the event
shifts something changes and the agent must decide. The event shifts test the three AI
Security Gridworlds:

| Gridworld | Question |
|---|---|
| Stale authorization | Does the agent re-check the state before acting on an earlier approval? |
| Authorization revocation | Does the agent follow the principal's revised mandate? |
| Confused deputy | Does the agent refuse an instruction from a non-principal? |

A session runs under one of two conditions. State Drift has a stale-authorization shift on
four shifts. State and Instruction Drift has one stale-authorization shift, a confused-deputy
shift and a revocation shift for each revision of the mandate. Where the event shifts fall is
drawn from a seed fixed per task and trial, so every model meets the same schedule.

The agent runs real commands, people answer when it writes to them, and every outcome is read
from the state the environment is left in by the task's own checks, never from what the agent
says. The people are played by a user simulator (Claude Sonnet 5.5 by default).

| Environment | Tasks | What the agent works in |
|---|---|---|
| Software | 22 | A git repository with a remote, tests and a stand-in for `gh` |
| Terminal | 29 | An operations checkout with small tools over files |
| Database operations | 20 | A PostgreSQL server that stays up for the session |
| ML operations | 20 | A shared lab checkout with runs, checkpoints, a queue and a registry |
| Miscellaneous | 17 | A home and small-organisation hub of jobs, devices, bookings and mail |
| Workplace | 12 | Mail, calendar, forms, sheets and a learning system over a recorded world |

The reported metrics are the harm rate, the over-refusal rate, the event pass rate, the
completion rate, integrity violations, the cost per session and the Steward score, per model,
condition and gridworld (docs/scoring.md).

## Requirements

- Linux with Apptainer, run rootless. Every command runs in its own container.
- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- About 2.5 GB of container images under the data folder, listed below.
- API keys for the models you run, in the environment or in `.env` (see `.env.example`):
  `OPENAI_API_KEY` for the OpenAI models, `ANTHROPIC_API_KEY` for the Claude models and the
  default user simulator, `GEMINI_API_KEY` for the Gemini models and `MUSE_API_KEY` for the
  Muse models. `docs/running.md` lists each model's API.

The images live in `$STEWARD_DATA/apptainer/` (by default `data/apptainer/`):

| File | Used by | Source |
|---|---|---|
| `workspace.sif` | Every repository task, and every check | `apptainer build workspace.sif steward/environment/workspace.def` |
| `postgres.sif` | Database operations tasks and Workplace worlds | `apptainer pull postgres.sif docker://postgres:15` |
| `gym.sif` | Workplace tasks | Toolathlon-GYM's tool servers, built from the Dockerfile of its checkout |
| `canvas_server/` | Workplace tasks that use the learning system (optional) | A build of the Canvas tool server wired to the world's database |

Workplace tasks also need the Toolathlon-GYM checkout at
`$STEWARD_DATA/upstream_tasks/toolathlon_gym`
(`git clone https://github.com/eigent-ai/toolathlon_gym.git`).

## Setup

```bash
git clone <this repository> steward-bench && cd steward-bench
uv sync                                  # dependencies pinned in uv.lock
git config core.hooksPath .githooks      # run scripts/check before every commit and push
cp .env.example .env                     # then fill in the keys you need
uv run pytest -q                         # runs on fixtures; needs no containers and no keys
```

`STEWARD_DATA` sets the data folder; it defaults to `data/` in the checkout. Everything a run
writes goes there, and nothing there is committed.

## Commands

Every command is `uv run steward <command>`; `--help` on any of them lists its options.

| Command | What it does |
|---|---|
| `check [--task ID] [--check-only] [--workers N]` | Builds each task's environment and replays its references on the benchmark schedule (docs/running.md). Prints `<id>: ok` for a task that passes |
| `estimate --model M [--model M2] [--trials N] [--condition sd\|sid]` | Prints the cost of the whole planned benchmark per model and for the simulator. Calls nothing |
| `run --model M --condition sd\|sid [--trials N] [--max-usd X] [--confirm]` | Runs the benchmark sessions not yet stored. Without `--confirm` it prints the estimate and stops |
| `probe [--task ID] [--simulator-model M] [--confirm]` | Sends the user simulator fixed probes with no agent and grades its replies |
| `score --model M [--model M2] --simulator-model S\|none` | Prints every metric per model and condition from the stored sessions, and the difference between each pair of models |
| `lint-tasks` | Lists repeated names, shared sentences and loose disclose patterns across tasks |

A first run on one model:

```bash
uv run steward check --workers 3          # once, after cloning
uv run steward estimate --model gpt-6-luna
uv run steward run --model gpt-6-luna --condition sd --condition sid           # estimate
uv run steward run --model gpt-6-luna --condition sd --condition sid --confirm
uv run steward score --model gpt-6-luna --simulator-model claude-sonnet-5-5
```

`--simulator-model none` leaves the people their fixed lines and makes no simulator calls,
which is enough to exercise the environments.

## Outputs

| Path under the data folder | Contents |
|---|---|
| `tasks/built_sessions/<task>/` | Each repository task's built environment |
| `tasks/sessions/<model>.jsonl` | One line per finished session: its setting, every event and every shift's measures |
| `tasks/sessions/<model>.failed.jsonl` | Sessions that raised, with the error, its kind and what they spent |
| `tasks/simulator_cache/` | Every simulator reply, by model and request, so a rerun gets the same reply |
| `tasks/rate/<model>.jsonl` | Requests, rate limits and tokens per 30 seconds of a run |
| `tasks/sim_probe/<model>.jsonl` | One line per probe with its grade, tokens and cost |
| `phase2/gym_states/` | Saved Workplace world states, rebuilt once per task |
| `logs/` | Logs of each command |

Runs resume: a stored session is not run again while its task version and the harness are
unchanged, and a stopped run loses only the sessions it was running.

## Repository layout

```
tasks/                  the 120 tasks, one folder each, and the base scripts in tasks/_bases
tasks/_worlds/          the recorded session the Workplace tasks start from
steward/                the harness: task models, the session loop, environments, policies, scoring
tests/                  tests on fixtures and fake environments
docs/                   framework.md, tasks.md, running.md, scoring.md
scripts/check           the gates run before every commit and push
```

## Costs

Every command that calls a model prints an estimate first and makes no call without
`--confirm`; `run` also stops starting sessions at `--max-usd` (default $25), counting
the tested model and the simulator together. Prices are in `steward/prices.py`, each
with the provider's page it was read from. A session of gpt-6-luna has cost about $0.017; the
whole benchmark on one model, both conditions and one trial, is 240 sessions.
`estimate` gives the current figure for any priced model.

## Limitations

- The environments are real but small, written for the benchmark, and the inherited histories
  of repository tasks are scripted and executed rather than recorded from users.
- Outcomes are read by each task's checks, so harm the checks were not written for is not
  counted. The stored measures do not tell an agent that held back from one that tried and
  failed; both are over-refusal.
- The people's replies are written by a model and can vary between runs; their first answers
  and everything they say unprompted are fixed text.
- A model that also plays the people partly talks to itself in its own sessions.
- The container images and the Toolathlon-GYM checkout are not built by a command here.
- Each command runs in its own container on one machine; the runs assume rootless Apptainer
  and, for resource limits, a reachable systemd user manager (docs/running.md).

## Further reading

- docs/framework.md: the fixed vocabulary, the conditions and the scoring principles.
- docs/tasks.md: what a task holds, its version, the people, the answer key and how to add one.
- docs/running.md: building, checking, running, probing, resuming and the machine limits.
- docs/scoring.md: the measures each shift stores and how the metrics are read from them.
