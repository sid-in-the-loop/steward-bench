# Analysis of the stored sessions

Two commands prepare the analysis that decides whether the benchmark is ready to scale (see
the conditions at the top of docs/next_up.md). Both read only the sessions that count in the
benchmark, as `steward/cases/catalogue.py` decides; replaced, earlier, set-aside and outside
sessions are left out. Both are deterministic: the same stored sessions and seed give the
same output. Both can be rerun at any time, also while a run is still adding sessions, since
a session line still being written is skipped.

## Checking the scorer's labels: bench-sample

Every measure rests on the labels the scorer gives each loop: whether a drift loop caused
harm, whether the agent went beyond its authority, whether it disclosed what it did, whom it
asked and how the loop ended. Before trusting any pattern built on them, a person reads a
sample of sessions and says whether those labels are right.

    steward bench-sample [--size 100] [--minimum 1] [--seed 0] [--model M ...]
                         [--out DIR] [--digests DIR]

Sessions are grouped by model, level and outcome: harm on at least one drift loop, drift
without harm, or no drift loop. Every group that has sessions gets at least `--minimum` of
them, and the rest of `--size` is shared out in proportion to group size by largest
remainder. Within a group the draw is seeded and taken over the sessions sorted by case,
setting and repeat, so the order sessions are stored in does not change it.

The output goes to `data/cases/analysis/validation/` by default:

| File | Contents |
|---|---|
| `sample.csv` | One row per sampled session: its identifier, model, case, level, setting, outcome, the labels of each drift loop, how its loops ended, whom it asked on loops without drift, the path of its digest, and empty `verdict` and `note` columns |
| `checklist.md` | The same sessions as a list to work through |

The digest of each sampled session is written to `data/cases/analysis/digests/` if it is not
there already, in the layout `steward bench-digest` uses. Write `correct` or `wrong` in the
verdict column and a note for anything wrong. A rerun keeps every verdict and note, matched
by model, case, setting and repeat, and keeps a checked session in the table even if a later
draw no longer picks it.

## What moves harm: bench-factors

    steward bench-factors [--reps 1000] [--seed 0] [--model M ...] [--out DIR]

This measures, for each model, how each factor of a setting changes harm. The unit is a drift
loop whose harm check ran. The factors are read from the stored setting, not from its
identifier:

| Level | Factors |
|---|---|
| I | The six single-factor experiments, each over its own settings only: length (2, 4, 16, 64 loops), users (1 to 4), drift loops (1, 4, 16), position of the drift loop (2, 9, 15), notes (accurate, out of date, missing) and how often users speak (rare, regular, frequent) |
| II | How many loops drift (1, 2, 4); where the drift loop falls (loops 2-6, 7-11, 12-16) |
| III | Notes; drift loops; users; whether the owner, bystander and manager are present; whether the owner warns, the bystander holds the out-of-date belief and the manager pushes; each present user's rate and share of unrelated messages; chat activity (0 to 3); which kinds of message and exchange were heard at the drift loop's handover; position |
| IV | The second change (unrelated, related, same object, or the owner's change carried for three loops); drift loops (1, 2); position |
| V | Which recording of the simulated owner made the change; position |

Loops of one session, and sessions of one case, are not independent, so the estimates count
cases equally and their intervals resample cases:

- A rate is the average over cases of each case's share of harmed drift loops.
- An effect compares two values of a factor: the highest against the lowest for an ordered
  factor, every value against the first for one that is not ordered (notes against accurate,
  the second change against unrelated, recordings against the first). It is the average,
  over cases that ran both values, of the difference in harm rate, so a case that ran only
  one value cannot move it.
- Every interval is a 95% percentile interval from a two-stage bootstrap: the cases are
  resampled, then the sessions within each value of each resampled case. The generator is
  seeded from `--seed` and the name of the comparison.
- Each effect is also given per case, with how many cases have higher, lower and equal harm.

A comparison is marked `raises harm` when, for every model, the interval lies above zero
and more cases have higher harm at the first value than lower. A case where harm is the same
at both values, which is often no harm at either, counts neither way: it is no evidence of a
direction. `lowers harm` is the same the other way, `not consistent` is anything else, and
`not measured for every model` means some model has no case with both values. A factor that
raises harm for every model is a candidate difficulty dial.

The levels are compared the same way, as a factor whose values are the levels. Level I mixes
its six experiments, so the steps run from Level I's baseline setting (16 loops, drift on
loop 9, two users, out-of-date notes), which is the setting Level II draws from, through
Levels II to V; the table also shows Level I as a whole.

The output goes to `data/cases/analysis/factors/` by default:

| File | Contents |
|---|---|
| `report.md` | Harm by level with intervals, each level against the one before, the candidate dials, the factors that consistently lower harm, every comparison's verdict, and counts of the data |
| `rates.csv` | Harm rate per model, level, factor and value, with its interval, the cases, sessions and loops behind it, and the share of sessions with harm |
| `effects.csv` | Each comparison per model: the effect, its interval, and the cases higher and lower |
| `by_case.csv` | Each comparison per model and case: the two rates and their difference |
| `verdicts.csv` | Each comparison's verdict over the models, with every model's effect |
| `quality.csv` | Sessions, sessions with drift, drift loops and drift loops without a harm check, per model |

A run over about 7,800 sessions with 1,000 resamples takes about two and a half minutes, and
the sample about one minute.

## What these do not settle

The harm labels are taken as the scorer gives them until the sample has been read. The
factor effects are associations within the settings the levels draw; Level III draws its
users independently, but a factor that is rare in the draw, such as a session whose only
user is the requester, may have too few cases for every model. Sessions of 64 loops are few
and take many tokens, so the length comparison rests on fewer sessions than the rest.
