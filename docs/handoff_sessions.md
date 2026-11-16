# Handoff: session experiments

Status as of 2026-11-15, 19:10 CST. Read `CLAUDE.md`, then `docs/cases.md` (sections "Sessions
of several work items" onward), then this file.

## Project question

When the state of delegated work drifts from what the handover says, does an agent preserve
other people's work without abandoning its own? Three models: `gpt-6-luna`, `gpt-5.6-luna`,
`gpt-5-mini` (OpenAI API, key in the git-ignored `.env`). User simulator: Claude Haiku 5.5
(`claude-haiku-5-5`), Anthropic key in the same `.env`.

## Scope

The world changes only between loops: every drift has happened before the requester hands
over the loop's task. Users speak only at the handover, before the task, and when the agent
asks them; nothing happens while the agent works. Users who act or message concurrently and
asynchronously belong to a separate, later benchmark of agent collaboration, not to this one.
The one exception is the `during` script of s8 and t8 (and the case `l3`), a single scripted
change just before the agent's first change in a drift loop; those cases are kept in stage 1
and left out of later stages.

## Formal definitions

| Symbol | Definition |
|---|---|
| Σ = (ℓ₁, …, ℓₙ) | Session of n loops, one environment, one conversation |
| sₜ, b, N | State at loop t; the agent's belief; the handover notes it comes from |
| gₜ, π₀ | Request of loop t; the routine procedure N describes |
| H(s) ∈ {0,1} | Harm, read from the state after the loop |
| dₜ = H(π₀(sₜ)) | Drift indicator. D = {t : dₜ = 1} are drift loops, the rest control loops |
| U ⊆ {R, O, B, M} | Requester (believes b, says proceed), owner (made the drift, states it when asked), bystander (no authority, approves), manager (authority, pushes speed) |
| ρ | How often O, B and M speak unprompted |
| Hₜ, Lₜ, Aₜ, Aₜᴼ, Cₜ, Rₜ | Harm; inspected before first write; asked any user before first write; asked O; request completed; report discloses the drift |

Baseline θ₀: n = 16, D = {9}, U = {R, O}, N = stale (out of date), ρ = regular.

| Experiment | Sweep | Estimand | Inference |
|---|---|---|---|
| E1 horizon | n ∈ {2,4,16,64}, D = {2},{3},{9},{33} | P(H given d=1) by n; Lₜ by t | Vigilance decay |
| E2 users | U ∈ {R},{R,O},{R,O,B},{R,O,B,M} | P(H given d=1), P(Aᴼ given d=1) | Information seeking, pressure |
| E3 drift count | D ∈ ∅, {9}, {3,7,11,15}, {1..16} | P(A or not C given d=0) at D=∅; P(H) on j-th drift loop | Over-caution, adaptation |
| E4 position | D ∈ {2},{9},{15} | P(H given d=1) by position | Habituation |
| E5 notes | N ∈ {accurate, stale, none} | P(H given d=1); stale minus none | Anchoring on misleading context |
| E6 rate | ρ ∈ {rare, regular, frequent}, U = {R,O,B} | P(H given d=1) by ρ | Repetition standing in for authority |
| X route | All sessions, by route ∈ {ask O, ask R only, inspect, none} | P(H given d=1, route) | Which behaviour protects (observational) |

In code, "item" = loop, `changed` = d, `Setting` = θ. `EXPERIMENTS` in
`steward/cases/sessions.py` defines the 16 distinct settings.

## Code (none of it committed; commit only when the user asks)

| File | Role |
|---|---|
| `steward/cases/sessions.py` | `Setting`, `EXPERIMENTS`, `speakers_before` (ρ schedule), `SessionSpec`, `Session` loop, per-loop `ItemResult`, `run_session` |
| `steward/cases/session_commands.py` | load, build, scripted check, `run_sessions` (resumable, appends per session), cost estimate, `current_sessions`, `item_rows` |
| `steward/cases/session_report.py` | Figures from the per-loop table, Wilson intervals, findings text with live counts |
| `steward/cases/session_report.html`, `session_findings.yaml` | Page template; the written findings (`{figure:model:category}` placeholders are filled with counts) |
| `steward/cli.py` | `session-build`, `session-run`, `session-table`, `session-report` |
| `cases/<id>/session.yaml`, `item.sh` (`during.sh` for s8, t8) | Session form of each case. Do not edit `case.yaml` or `setup.sh`: they are hashed into versions |
| `tests/cases/test_sessions.py` | 48 tests in `tests/cases` pass; ruff and mypy on `steward` clean |

Loop rules worth knowing: text with no "?" that addresses no other user is the report; a
question to R after `done` passes is a closing question (R says "that is all"); a user is
addressed by first name ("Mara," or "@mara" or ", Mara?"); a second question to the same user
in one loop goes to the simulator; two unanswered messages end the loop as `waiting`; 20
turns per loop maximum. A change to the loop rules must bump `PROTOCOL` in `sessions.py`,
which makes stored sessions out of date.

## Cases

15 cases, all pass `session-build`: s1 s2 s4 s5 s7 s8, t1 t3 t4 t5 t7 t8, w1 w2 w4. Dropped
from the original set: s3, s6, t2, t6, w3 (at floor or ceiling, or duplicates). Weak
conversions to review with the user: w2 (the mail hold announces itself; harm needs a
deliberate override; `done` counts a held send), s4 (up to 64 releases), s7 (owner re-types
data into a generated file), t3 and t4 (`done` definitions; t4 task wording differs from the
case), s8 (adds an entry instead of ticking a box). Details are in the subagent reports
summarised in the conversation; read each `session.yaml` before changing it.

## Runs

- Stage 1 (E1 to E6, 15 cases, 3 models, 2 repeats, 1,440 sessions planned). At handoff:
  792 current sessions, $19.25 spent. Complete: s1, s2. Partial: s4, s5, s7, t4, t5, t7,
  t8. Not started: s8, t1, t3, w1, w2, w4.
- Sessions: `data/cases/sessions/<model>.jsonl`. Logs: `data/cases/sessions/logs/`.
- Budget cap $100 total for session experiments (user, 2026-11-15). Every model-calling
  command prints an estimate first and needs `--confirm`.
- Compute: the user starts one-hour interactive jobs (16 CPUs, node gpub073 at handoff,
  job 24029781). Run from the node with five workers per model:

      cd /u/ssredharan/steward-bench && export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
      for m in gpt-6-luna gpt-5.6-luna gpt-5-mini; do
        nohup .venv/bin/steward session-run --model $m --trials 2 --workers 5 --confirm \
          > data/cases/sessions/logs/run-$m.log 2>&1 < /dev/null &
      done

  Runs resume where they stopped. The current node run was limited to `--case w4 w2 w1 t8
  t7 t5 t4`; drop the `--case` flags next time.
- A stray run started on login node dt-login01 may still be running and cannot be reached
  from compute nodes. The user was asked to stop it with
  `kill $(pgrep -u $USER -f "[s]teward session-run")` on the login node. Duplicate sessions
  are valid repeats.
- On the login node: never more than two workers per model, process cap 1,000, never
  `pkill -f`.

## Results page

Rebuild and republish after runs:

    uv run steward session-report --out reports/results/sessions.html

Published (private) at https://claude.ai/artifact/NV94YiqrqyHS26XUeDw7Ti, slug
`adfdb48d-61c1-4ea5-b83f-f20c7cf7dcf5`; get the live version with `get_artifact` before
republishing. The wording in `session_findings.yaml` is written by hand and must be revised
when counts move; the counts fill in by themselves. The user judged the page "better, not
the best".

Readings at 676 sessions, mostly software cases, all provisional:
- Aᴼ is sufficient: H = 0 on every drift loop where O was asked first, but P(Aᴼ) is about 0.2.
- Stale notes are worse than none (E5) for all three models.
- M raises H for gpt-6-luna (E2); intervals overlap.
- gpt-5-mini adapts when every loop drifts; gpt-5.6-luna does not (E3).
- gpt-5.6-luna leaves C = 0 on about 25% of control loops.
- P(R = 1 given H = 1) is 9 to 35%.
- E1, E4 and E6 show no effect yet.

## Next, in order

1. Finish stage 1 on compute nodes, rebuild the page, revise the findings text.
2. Stage 2: D drawn from a seed (several drift patterns per case).
3. Stage 3: users as parameter vectors (knowledge, authority, stance, rate, consistency)
   instead of four fixed roles, speaking only at handover or when asked (see Scope).
4. Stage 4 (s1, t5, w4 first): several drifts per loop by different users, on the same,
   related or unrelated objects, and drifts that persist into later loops.
5. New domains proposed, not started: cloud infrastructure (terminal), course administration
   and finance operations (workplace world).

## Working with this user

- Plain, short, formal wording. Formal definitions with symbols are wanted. No invented or
  informal labels ("twin", "catch", "level", "changed loop"); use the terms above.
- Results pages lead with findings and analysis, not one chart per setting; see the memory
  note on webpage style.
- Ask before spending past the cap, deleting data, or changing scope.
