# Adding a case

This is the guide for adding a task to the benchmark. docs/cases.md is the reference for
every field of a case, and docs/levels.md for what each level varies and how sessions are
run and read.

## What a case is

A case is one routine job that an agent has taken over from a previous agent, in a real
environment, with other people whose work the routine can destroy.

- The routine. The previous agent did it correctly and left notes; the requester asks for it
  again every loop. Examples: rebuild a branch, apply retention, refresh a reporting table.
- The drift. Since the notes were written, the owner has changed something the notes do not
  know about, so the routine as written destroys the owner's work. The change is made before
  the loop is handed over, never while the agent works.
- The people. A requester who asks and believes the notes; an owner whose work is at risk;
  a bystander with no say; a manager who wants speed.
- The safe action. A way to finish the routine that keeps the owner's work, which a careful
  agent can find by looking, by reasoning over what it sees, or only by asking.
- The checks. Scripts that read the environment after a loop and say whether the owner's
  work was harmed, whether the agent stayed within its authority, and whether the routine
  was done. Nothing is judged from the agent's words except whether its report mentions the
  change.

A good case has a harmful action that looks routine, a drift the routine's own tools do not
announce, and a harm check that never fires when the agent behaves safely, on drift loops
and control loops alike. A case where every model always causes harm, or never does, tells
nothing; aim for a drift that some care finds.

## Where things are

```
cases/
  _bases/                 one script per environment family; builds the repository, tools and data
  <domain>-<hash>/        one folder per case, named by case_identifier(domain, title)
    case.yaml             people, situation, inherited history, fixed lines, measures, references
    setup.sh              what other people did after the previous agent's last step
    session.yaml          the session form: task, fixed answers, checks, Level IV and V fields
    item.sh               run before each loop: makes the trigger and, on a drift loop, the change
    bank.yaml             wordings users can say unprompted (written by bank-build)
    recordings/NN.json    recorded owners for Level V (written by owner-record)
steward/cases/            the code: case model, session loop, levels, bank, recorder, runner, viewer
tests/cases/              tests on fake environments; no containers, no model calls
docs/                     cases.md (reference), levels.md (levels and running), this file
data/cases/               built environments and stored sessions; not in git
```

The domains and the base each uses:

| Domain | Key | Base script | Environment |
|---|---|---|---|
| Software | `swe` | `cases/_bases/tally.sh` | A git repository with a remote, tests and a stand-in for `gh` |
| Terminal | `terminal` | `cases/_bases/ops.sh` | An operations checkout with small command-line tools over files |
| Workplace | `workplace` | none: a recorded world | Mail, calendar, sheets, forms and Canvas tools over a world's database |
| ML operations | `mlops` | `cases/_bases/lab.sh` | A shared lab checkout: runs, checkpoints with hashes, a queue, a registry |
| Database operations | `dbops` | `cases/_bases/warehouse_db.sh` | A Postgres server that stays up for the session, with tools that call `psql` |
| Miscellaneous | `misc` | `cases/_bases/home.sh` | A home hub: small command-line tools over files for bookings, posts, devices, access codes, chats, calendars, orders, payments, mail and a nightly job runner |

A new case goes in the domain whose environment it needs. A new tool or data set that
several cases will share goes in that domain's base script; anything only one case needs
goes in its `setup.sh` or `item.sh`. A new domain is a new base script and a new key in
`Domain` in `steward/cases/case.py`.

## Steps

1. Start from a working case of the domain. The command copies it into a folder named for
   the new title and sets the identifier:

       steward case-new --domain mlops --title "Archiving runs that a paper still cites"

2. Rewrite `case.yaml`: the people, the situation, the inherited history of the previous
   agent doing the routine correctly, the fixed lines, the `look`, `change` and `disclose`
   patterns, the `harm`, `authority` and `done` checks, and the safe and harmful references.
3. Rewrite `setup.sh` and `item.sh`. The item script runs before every loop with `ITEM`,
   `CHANGED` and `ITEMS` set. It first settles what the owner changed on earlier loops, then
   creates this loop's trigger, applies the owner's change when `CHANGED=1`, and prints the
   `name=value` lines the texts use. Keep what the checks compare against (ids, hashes) where
   the routine does not reach. `CHANGED` and the other event flags are shell variables of the
   script, not exported: give one to a program of the task's own as an argument or a prefix.
   Run every program of the agent's tree or home (a tool, a job, `make`, `pytest`, a script
   given to an interpreter) as `as_agent PROGRAM`, in item scripts and checks alike, and
   write a negated test in a check as `absent TEST` rather than `! TEST`; docs/cases.md, "The
   answer key and idle runs", says why, and `tests/cases/test_agent_code.py` checks both.
4. Rewrite `session.yaml`: the task the requester gives each loop, each user's fixed answer,
   the lines users say unprompted at Level I, the accurate handover note, and the checks.
5. For Level IV add `carries` and `second`, and for Level V add `generated`; docs/levels.md,
   "What a case provides for Levels IV and V", says what the item script must do for each.
6. Build and check it. This builds the environment and runs both references through the
   single run, the session, every kind of second change, a carried change and the first
   recording; every line must say ok:

       steward case-check --case <id> --build

7. Write its message bank, read it, and commit it:

       steward bank-build --case <id> --confirm

8. Record simulated owners for Level V, read the recordings, and commit them:

       steward owner-record --case <id> --count 3 --confirm

9. See what the case now provides at each level, and what the benchmark would run for it:

       steward case-check --case <id>
       steward bench-run --case <id>

Steps 7 and 8 call Claude Haiku 5.5 and cost a few cents a case. Nothing else in this list
calls a model.

## What the check requires

`case-check --build` fails a case when its safe reference leaves harm or leaves the routine
undone, when its harmful reference does not harm the owner on exactly the drift loops, when
an action in a reference gets no answer from the environment, when the safe reference harms
the second person, or when the harmful reference harms the second person anywhere other
than the kinds listed in `routine_harms`. A recording is kept only when replaying it is a
real drift by the same test.

## Reading a new case's sessions

Run a few sessions and read them before running many:

    steward bench-run --case <id> --pilot --confirm
    steward bench-view --out view --case <id>

In the viewer, check on a drift loop that the change is discoverable the way the case
intends, that users say what their settings say they should, and that the sentence at the
top of each loop matches what the transcript shows.
