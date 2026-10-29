# Blockers

## Stage C2 cannot run: the repository is public

The brief (section 3) requires the repository to be private, because C2 shards carry windowed
renderings of gated data (SWE-chat, mle-traj) and section 4.7 forbids pushing raw gated data
anywhere but a private repository. On 2026-10-28 the GitHub API reports
`sid-in-the-loop/steward-bench` as public (`"private": false`). So the handoff/c2 branch was not
pushed and no cloud sessions were started.

What still happened: `steward c2-handoff` builds the shards locally in data/handoff/c2, and
docs/stage_c2_cloud.md describes the cloud procedure. Ranking falls back to C1 scores for every
trajectory, as section 8 allows. The C1 to C2 agreement numbers in PILOT.md and the report are
missing.

To unblock it: make the repository private (GitHub, Settings, Danger zone, Change visibility), then
run the steps in docs/stage_c2_cloud.md under "Running C2 from the cluster": push handoff/c2, start
at most six `claude --cloud` sessions at a time, fetch the results branches, then
`steward c2-merge`, `steward rank` and `steward analyze`.

Because C2 outputs are missing, the stop condition in brief section 2.3 is not met.
