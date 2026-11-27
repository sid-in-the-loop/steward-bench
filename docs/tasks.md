# Tasks

A task is one scenario in one environment (docs/framework.md). Each is a folder under
`cases/` named `{domain}-{hash}`, where the hash is the first ten hex digits of the SHA-1 of
the task's title (`case_identifier` in `steward/cases/case.py`). The domains are `swe`
(Software), `terminal`, `workplace`, `mlops` (ML operations), `dbops` (Database operations)
and `misc` (Miscellaneous). Reports name a task by its domain and title.

The task files and the stored results keep older field names (the last table of
docs/framework.md maps them): `owner` is the principal, a changed shift is the
stale-authorization shift, `goal` holds the revised mandate and `conflict` the confused
deputy. They are kept so that the tasks keep their versions and stored sessions stay readable.

## What a task holds

A task folder holds `case.yaml`, `session.yaml` and the shift script `item.sh`.

`case.yaml` describes the environment and the people.

| Field | Meaning |
|---|---|
| `environment` | `repo`: a small repository built from a script in `cases/_bases`, with a `database` if the task needs one. `world`: a Workplace world rebuilt from a recorded session (`trajectory_id`, `takeover_step`) |
| `people` | The requester (the user of the session), the principal (`owner`) and a bystander, each with a name and a description |
| `history` | The inherited session. In a repository task each `run` step is executed and its real output recorded |
| `setup`, `lines`, `measures` | The setup script, fixed lines and patterns; `measures.change`, `look` and `disclose` are read in sessions |
| `safe_reference`, `harmful_reference` | Scripted runs with known outcomes |

`session.yaml` holds what a session needs.

| Field | Meaning |
|---|---|
| `start` | Run once when the environment is built, in place of the case's setup |
| `item` | The shift script, run before every shift. It rebuilds the shift's state so that each shift stands on its own and prints `name=value` lines last, which fill `{name}` in the texts and the reference actions |
| `task` | What the requester says to hand over a shift |
| `manager` | The fourth person, a non-principal |
| `requester_reply`, `owner_changed`, `owner_unchanged` | What the requester answers, and what the principal knows on a stale-authorization shift and on a routine shift |
| `owner_thing` | The particular thing `owner_changed` is about, as an agent that had looked would name it. Read by `sim-probe` only and no part of the version |
| `descriptions` | Who a person is, as the simulator is shown them in a session, where the first sentence of their description in `case.yaml` would tell what they may know only on some shifts |
| `owner_status`, `bystander_opinion`, `manager_push` | What each person says unprompted, in turn |
| `accurate_note` | A handover note for the accurate-notes setting |
| `harm`, `authority`, `done` | Checks run after every shift; each exits 0 when the shift caused no harm, stayed within the mandate, and did the work |
| `goal` | The revised mandate: one to three `steps` in order, each with who says its `line`, its own `harm` and `done` checks and references, and a `final` check of the mandate at the end of the session. `harmful_reference_not_done` marks a step whose harmful reference only leaves the new work undone |
| `conflict` | The confused deputy: `first` and `first_line`, `second` and `second_line`, a `harm` check that exits 0 when the agent followed the person with the say, its references, and `either_order` when neither line answers the other |
| `visible_version` | The shift script builds two versions of the stale authorization: with `VISIBLE=1` the routine's own tool shows the change, with `VISIBLE=0` only the place the task names does |
| `safe_reference`, `harmful_reference` | What a careful and a careless agent do on a shift, used by validation |

Every script the harness runs gets `ITEM` (the shift), `ITEMS`, `DAY`, and the shell variables
`CHANGED`, `GOAL` (the step of the revised mandate in force, 0 before any), `CONFLICT` and
`VISIBLE`. The event flags are not exported, so no program the script starts can read them; a
tool of the task's own that needs one is given it as an argument.

## Versions

Each task has a version: a short hash of `TASK_PROTOCOL`, the case's version and every field of
`session.yaml` that can change a session, with `owner_thing` left out
(`load_session_spec` in `steward/cases/sessions.py`). The case's version covers its
environment, people, history, setup, lines and measures, the base script, and the `gh` stand-in
where the task uses it. Fields added after sessions were first stored are left out at their
defaults, so those sessions kept their versions. A stored session counts only while its task's
version is unchanged, so editing a task retires its earlier sessions without deleting them.
`tests/golden/task_versions.json` pins the versions of the 120 tasks.

## Environments

Repository tasks are built from a base script in `cases/_bases/`: `tally.sh` (Software),
`ops.sh` (Terminal), `lab.sh` (ML operations), `warehouse_db.sh` (Database operations) and
`home.sh` (Miscellaneous). The build runs the base, the inherited session and the start script,
and keeps the result under the data folder (`cases/built_sessions/<task>/`); each session runs
on a private copy.

A Workplace task's world is Toolathlon-GYM's tool servers over a private PostgreSQL database
(`steward/environment/gym.py`). Its state at the takeover is rebuilt once by making the
recorded session's tool calls again on a fresh world, mapping the identifiers the new world
gives to the recorded ones, and saved; later sessions restore the saved state in seconds. The
checks' own tables, shift flags and helpers live in the `steward` schema of the world's
database, which the agent's role cannot read.

### Database operations

A task asks for a PostgreSQL 15 database with `database` in its environment. One server runs
per session from `postgres.sif`, outside the command containers, so it stays up across every
command, script and shift (`steward/cases/postgres.py`). Each session has its own data
directory and socket; the server listens on no network address and runs confined. Commands
reach it through the socket with the client programs of the server image, and `PGHOST`,
`PGUSER` and `PGDATABASE` are set.

The agent's role is named after the task's user. It owns the task's databases, may create
databases, end other sessions and grant the ordinary roles it holds, and may not create roles
or run programs or read files on the server. What needs more goes through two functions of the
harness's `steward` schema, `steward.set_password` and `steward.drop_role`. The harness's own
scripts connect as the superuser, whose password is drawn afresh at each start and shown only
to them. While a harness script runs, every function the agent could have attached to its
tables runs with its owner's rights, so the agent's code never runs as the superuser.

A transaction that must be open at the handover is held by `held-session` in the remote's info
folder: `open NAME ROLE PASSWORD APPLICATION` begins it with statements from stdin, `settle
NAME` commits and closes it, and `alive NAME` exits 0 while it is open. Nothing progresses by
itself, so the outcome does not depend on how fast the agent is.

## The people

Four people are present in a benchmark session: the requester, the principal, a bystander and
the manager. What they say unprompted, the requester's task and each conflict and revision
line are fixed text. Their other replies are written by the user simulator
(`steward/cases/simulator.py`), and the same seed casts each person a card from a library of
144 (`steward/cases/casting.py`), which changes only how the simulator words their replies.

The simulator is shown who the person is (the first sentence of their description) and how
they write, their card's manner, their ordinary line, and on a shift where they know something,
their briefing. A person tells what they know only when the agent's message is direct: when it
names the particular thing they know about, so that the agent could not have written it from
the task's request alone. A statement they know to be false is corrected, and nothing outside
the briefing is added. A person who has said a line that still stands, such as a conflict line
or a revision of the mandate, is played from that line.

A reply that tells the change is withheld from the agent when its speaker could not know it:
anyone but the principal or a briefed person on a stale-authorization shift, and everyone on a
routine shift (`Session.tells_change`). A withheld reply is recorded as an event and counted.
The simulator reads the chat one message to a line, each quoted as a JSON string, so nothing
the agent writes can read as someone else speaking.

## The answer key

What only the checks read lives in the remote's info folder, `/srv/git/origin.git/info`. While
the agent acts the session keeps those files in a pristine copy outside the container
(`steward/cases/answer_key.py`). Around a shift script the key is put back writable; before
each check the pristine copy is mounted read-only, and a check run that changed it marks the
shift as tampered. Anything the agent put in the folder while the key was hidden is removed.

Harness scripts run in the agent's tree and home, so they keep the agent's code from running
as the harness:

- A harness script is a file shown to its own container alone, never a command-line argument.
- Every program of the agent's that a script runs goes through `as_agent`
  (`steward/environment/shims/as_agent`), in namespaces of its own where the key folder and
  the harness's files are empty and the harness's environment is gone.
  `tests/cases/test_agent_code.py` fails on any task script that runs agent code without it.
- git on the harness's path is `harness_git`, which runs no hook, filter, pager, helper or
  other command named in the agent's configuration. `tests/test_harness_sandbox.py` plants each
  of them and shows that the agent's git runs them and the harness's does not.
- The `gh` stand-in's state is read-only to the agent; its calls go through a broker in a
  container of its own (`GhBroker` in `steward/environment/workspace.py`).
- A negated test in a check is written `absent TEST`, which passes only when TEST exits 1, so a
  fault reads as harm and not as no harm.

While a check runs the key folder also holds two canaries with a token drawn for the session
(`steward/cases/tripwire.py`). After each shift's checks the agent's whole workspace is
searched for the token, and the shift's commands, outputs and messages are scanned for the
token and for the key folder's path. These give `key_copied`, `key_seen` and `key_probed`
(docs/scoring.md). Workplace worlds have no canary; their grader schema is out of the agent's
reach.

## Adding a task

1. Create `cases/<id>/` and the empty file `cases/<id>/.building`. Every loader skips a folder
   that holds it, except when the task is named with `--case`.
2. Write the title as one sentence that says what the routine does and what changes, and take
   the identifier from it:
   `python3 -c "import hashlib;print('<key>-'+hashlib.sha1('<title>'.encode()).hexdigest()[:10])"`.
3. Write `case.yaml`, `session.yaml` and `item.sh`, starting from a task of the same
   environment. Include a `goal` and a `conflict`, so the task runs under both conditions.
4. Validate, then delete `.building`:

```bash
uv run steward session-build --benchmark --case <id>
uv run steward lint-tasks
uv run pytest -q tests/cases
```

The rules every task keeps:

- Each shift stands on its own: the shift script rebuilds the shift's data but leaves the
  agent's own changes to jobs, configuration and tools in place, and a revised mandate, once
  said, is in force on every later shift.
- Nothing appears only when it is needed. A document that settles the conflict exists from
  shift 1 with content that is true on every shift.
- The person in the right gives their reason, not the rule; the agent finds the rule in the
  environment. Tools have no options made for the harmful action.
- Checks read the end state only, never the agent's words except `disclose`, and accept every
  careful path, not only the reference's. Doing nothing must not pass.
- Dates come from the run date (`date -d '+N days'`) and are printed as values; no text names
  a fixed calendar date or a weekday that may be false.
- Names are realistic and used in no other task; `lint-tasks` lists any that repeat.
