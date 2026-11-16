# Handoff: the session benchmark

Status as of 2026-11-16, morning. Read README.md, then docs/levels.md, docs/run_plan.md and
docs/adding_a_case.md, then this file.

## Project question

When the state of delegated work has drifted from what the handover says, does an agent
preserve other people's work without abandoning its own? Tested models: `gpt-6-luna`,
`gpt-5.6-luna` (OpenAI API) and `claude-haiku-5-5` (Anthropic API, the same price as
`gpt-6-luna`); keys in the git-ignored `.env`. Claude Haiku 5.5 also writes the message
banks, answers users' follow-up questions and plays the recorded owners. `gpt-5-mini` was
dropped on 2026-11-15 for cost; its stored sessions are kept as data.

## Scope

The world changes only between loops: every drift has happened before the requester hands
over the loop's task. Users speak only at the handover, before the task, and when the agent
asks them; nothing happens while the agent works. Users who act or message at the same time
as the agent belong to a separate, later benchmark of agent collaboration. The two session
cases with a `during` script (a change just before the agent's first change in a drift loop)
are kept for Level I only and are not benchmark cases.

## Formal definitions

| Symbol | Definition |
|---|---|
| Σ = (ℓ₁, …, ℓₙ) | Session of n loops, one environment, one conversation |
| sₜ, b, N | State at loop t; the agent's belief; the handover notes it comes from |
| gₜ, π₀ | Request of loop t; the routine procedure N describes |
| H(s) ∈ {0,1} | Harm to the owner, read from the state after the loop; H₂ the same for a second person |
| dₜ = H(π₀(sₜ)) | Drift indicator. D = {t : dₜ = 1} are drift loops, the rest control loops |
| U | The users present: requester R (believes b), owner O (made the drift), bystander B (no authority), manager M (pushes speed) |
| uᵢ = (rᵢ, qᵢ, kᵢ, pᵢ, wᵢ) | At Level III, a user's rate of speaking at a handover, share of unrelated messages, knowledge, push, and whether the owner warns |
| Hₜ, Lₜ, Aₜ, Aₜᴼ, Cₜ, Rₜ | Harm; inspected before first write; asked any user before first write; asked O; request completed; report discloses the drift |

Baseline θ₀: n = 16, D = {9}, U = {R, O}, N out of date, users speak at the regular rate.
Level I varies one of n, U, |D|, the position of D, N and the rate from θ₀; Levels II to V
are described in docs/levels.md. In code, "item" = loop, `changed` = d, `Setting` = θ.

## State of the repository

Everything is on `main` and pushed. 25 benchmark cases, five per domain, each building and
passing its references through the single run, the session and every level it provides
(docs/run_plan.md lists them). The commands are in README.md.

| Part | Where |
|---|---|
| Case model and identifiers | `steward/cases/case.py`; folders are `case_identifier(domain, title)`; `data/cases/former_identifiers.json` maps the old short codes |
| Session loop and measures | `steward/cases/sessions.py` |
| Levels, users, message bank | `steward/cases/levels.py`, `level_settings.py`, `bank.py`; banks in `cases/<id>/bank.yaml` |
| Levels IV and V in a case | `second`, `carries`, `generated` in `session.yaml`; `levels.sh` beside an unchanged `item.sh` in the 13 cases that had stored sessions |
| Recorded owners | `steward/cases/recorder.py`, `steward owner-record`; `cases/<id>/recordings/NN.json` |
| Session-long Postgres | `steward/cases/postgres.py`, used by the database cases |
| Planning, running, limit, pilot | `steward/cases/bench.py`, `session_commands.py`; `steward bench-run`, `bench-status` |
| Viewer | `steward/cases/bench_view.py`, `bench_view.html`; `steward bench-view` |
| Adding a case | `steward/cases/authoring.py`; `steward case-new`, `case-check`; docs/adding_a_case.md |

## Data

- Sessions: `data/cases/sessions/<model>.jsonl`, appended per session. About 1,280 Level I
  sessions of the two luna models on the cases that existed before are stored and current.
- A sample of ten sessions (one ML operations case through all five levels, on `gpt-6-luna`
  and `claude-haiku-5-5`) is stored and published at
  https://claude.ai/artifact/NCzumkvoK9xFabU5T9joP8. Its pilot summary was moved to
  `data/cases/sample_pilot_mlops.json`, so `bench-run` still requires the real pilot.
- Logs: `data/cases/sessions/logs/`.

## Spend

The cap for the benchmark is $140 in total across every API. Spent: about $23.50 before
2026-11-16 (Level I, OpenAI), and about $0.50 overnight (the sample sessions, the banks and
the recordings). The full run is priced at about $137 with Level III at 20 settings per case.
Every model-calling command prints an estimate and needs `--confirm`; `bench-run` stops
starting sessions at `--max-usd`.

## Compute

Run on compute nodes. The user starts interactive jobs; from the node run the command in
docs/run_plan.md, step 4. On the login node use at most two workers per model, keep the
process count under 600 of the 1,000 allowed, and never use `pkill -f`.

## Known limits

- Claude Haiku 5.5 is both a tested model and the users' model.
- The backfill case has no Level V.
- Stored Level IV and V sessions are tied to the version of the case's levels script; a
  change to it reruns them.
- A recorded owner is kept only when its change is a real drift; a case whose goal leaves the
  change underspecified gets none (the rebuild case's goal had to be made concrete).

## Next, in order

1. The steps of docs/run_plan.md: record the third owner per case, pilot, review, full run.
2. Analysis of the stored sessions; the old Level I results page is
   https://claude.ai/artifact/NV94YiqrqyHS26XUeDw7Ti and is to be rebuilt from the new data.
3. New cases with `steward case-new`, once the analysis shows which kinds of drift separate
   the models.
4. The separate collaboration benchmark, with users acting while the agent works.

## Working with this user

- Plain, short, formal wording. Formal definitions with symbols are wanted. No invented or
  informal labels; use the terms above.
- Results pages lead with findings and analysis, not one chart per setting.
- Ask before spending past the cap, deleting data, or changing scope.
- Commit each finished change through the hooks and push straight after.
