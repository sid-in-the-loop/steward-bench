# Blockers

## Stage C2: cloud sessions cannot push results

Status: blocking. Stage C2 has no results, so the stop condition in brief section 2.3 is not met.

What happened: `claude --cloud` creates sessions as expected. `steward c2-launch` runs it under
`script`, because it needs a terminal, and records each session URL in
data/handoff/c2_pilot_launches.jsonl. Five sessions were started:

| Session | URL | Asked to push |
|---|---|---|
| C2 pilot, shard 000 | https://claude.ai/code/session_01PASRkyk9gwt4QryxV4vHEB | results/c2-pilot-shard-000 |
| C2 pilot, shard 001 | https://claude.ai/code/session_01PASRkz9aqZRkitYvAfPKWp | results/c2-pilot-shard-001 |
| Connectivity probe | https://claude.ai/code/session_01PASRkyALx1qcY1TSzhQyzh | nothing |
| Diagnostic 1 | https://claude.ai/code/session_01PASRm1oG2SqwSKvo37deXQ | results/c2-diagnostic and claude/c2-diagnostic |
| Diagnostic 2 | https://claude.ai/code/session_01PASRkx5zzPh2pJ7CWbGsKU | claude/c2-diag2 and results/c2-diag2 |

Every session went idle, and none pushed a branch, not even under the `claude/` prefix. The two
pilot sessions were also sent a follow-up message, asking them to continue and to report any
blocker on a branch. Nothing arrived. Cloud sessions cannot message this machine back, so their
transcripts can only be read at the URLs above.

Likely cause: the cloud sessions have read access to this public repository but no write access.
The Claude GitHub app is probably not installed on sid-in-the-loop/steward-bench, or not granted
write permission. A failed `uv sync` would not explain the diagnostic sessions, whose first job
was a plain git push.

What is ready:
- data/handoff/c2/shard_*.jsonl is built from the full C1 results. It holds the top 60 per domain
  by the sum of the two highest axis scores, at most 25 trajectories per shard, with renderings
  passed through secret redaction. The shards are pushed on branch handoff/c2.
- The pilot shards are in data/handoff/c2_pilot/ and on the same branch.

To unblock:
1. Open one of the session URLs above and read why it stopped.
2. If it is a permission problem, install or authorize the Claude GitHub app for this
   repository with write access. If cloud sessions turn out to be limited to `claude/` branches,
   change the branch prefixes in steward/cli.py (`_c2_locations`).
3. Run `uv run steward c2-launch --pilot`. When the pilot branches exist, run
   `uv run steward c2-fetch --pilot`, `uv run steward c2-merge --pilot` and
   `uv run steward pilot-report`. Then run `uv run steward c2-launch` (re-run it as sessions
   finish; it keeps six running), `uv run steward c2-fetch`, `uv run steward c2-merge`,
   `uv run steward rank` and `uv run steward analyze`.

The launch log refuses to start a shard twice. To relaunch the pilot shards, first delete
data/handoff/c2_pilot_launches.jsonl.

Effect until then:
- Ranking uses C1 scores for every trajectory, which section 8 allows. The ranked columns
  jump_in_phase, buttons, checkability and right_behavior_agreement are empty.
- history_tokens_at_jump_in is computed from the trajectory.
- PILOT.md and the report have no C1 to C2 agreement.

## Resolved: the public repository

The brief requires a private repository for the C2 handoff, and this repository is public. At
00:33 on 2026-10-29 the owner said the public or private question is not an issue and to keep
going. Shard renderings go through steward/filter/redact.py before they are written.
