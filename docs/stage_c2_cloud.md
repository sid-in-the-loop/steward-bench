# Stage C2 in a cloud session

This is the exact procedure a cloud session started with `claude --cloud` follows for one shard.
It also covers the cluster side: building the shards, starting the sessions and merging the
results. Brief sections 3.2 and 6.5 define the stage.

The brief requires a private repository, because shards carry windowed renderings of gated data
(brief 4.7). For this run the owner waived that requirement (reports/DECISIONS.md). Renderings
are passed through secret redaction (steward/filter/redact.py) before shards are written.

## Inside the cloud session (one shard)

The cluster starts the session with a prompt that names three things: the shard, the results
file and the results branch.

```
Stage C2 for data/handoff/c2/shard_007.jsonl on branch handoff/c2. Follow
docs/stage_c2_cloud.md exactly. Write results to data/results/c2/shard_007.jsonl. Push results
to branch results/c2-shard-007.
```

Below, SHARD, RESULTS and BRANCH stand for those three values. Follow these steps in order. Do
not change prompts, schemas or code.

1. Check out the handoff branch and install the package.

   ```bash
   git fetch origin handoff/c2 && git checkout handoff/c2
   pip install uv && uv sync
   ```

2. Split the shard into one input file per trajectory, and write out the result schema.

   ```bash
   uv run steward c2-split SHARD /tmp/c2/inputs
   uv run steward c2-schema > /tmp/c2/schema.json
   ```

3. For each input file in /tmp/c2/inputs, start one subagent on the opus model. Give it exactly
   three things: the full text of prompts/stage_c2.md, the JSON schema in /tmp/c2/schema.json,
   and that one input file. The input file holds the trajectory_id, the first-pass (C1) result
   and the windowed rendering. Tell the subagent to return only one JSON object that matches the
   schema, with trajectory_id copied from the input, and with every excerpt copied verbatim from
   the rendering. Each subagent sees one trajectory only. Run at most five subagents at a time.

4. Append each returned JSON object as one line to RESULTS, creating the directory if needed.
   Write only the object, without code fences.

5. Validate the results against the shard.

   ```bash
   uv run python -m steward.filter.validate c2 RESULTS --shard SHARD
   ```

   The validator checks:
   - the schema;
   - that every score of 2 or 3 cites a step and an excerpt;
   - that every excerpt appears verbatim in the rendering;
   - that every trajectory in the shard has a result;
   - that no trajectory_id is unknown or repeated.

6. For each trajectory the validator reports, delete its line, then start one new subagent for
   it with the validator's error message added to the instructions. Re-run each failure once
   only, then validate again. Any line that still fails stays out of the file.

7. Commit only the results file, and push it to the results branch.

   ```bash
   git checkout -b BRANCH
   git add -f RESULTS
   git commit -m "Add Stage C2 results for SHARD"
   git push origin BRANCH
   ```

8. Reply with one line: the shard, the number of results written, and the number of validation
   errors remaining.

The session must not write titles, names or descriptions of benchmark scenarios. It must not
edit the handoff branch, and it must not push anything other than the results file.

### If something fails

Nobody watches the session, so it must not stop to ask questions.

- If `uv sync` fails, use any Python 3.12 or later: run `pip install -e .`, then call
  `python -m steward.cli` wherever these steps say `uv run steward`.
- If subagents on opus cannot be started, do the deep read in the session itself, one
  trajectory at a time, with the same three inputs.
- If the session is truly blocked, write data/results/c2/BLOCKED_<shard number>.md. Say which
  step failed and give the exact error. Push it to a branch named like the results branch with
  `-blocked` appended. That branch is the only channel back to the cluster.

## On the cluster

Every command below works with the pilot too: add `--pilot`. The pilot uses the directories
data/handoff/c2_pilot and data/results/c2_pilot and the branches results/c2-pilot-shard-NNN, and
`uv run steward pilot-report` then writes reports/PILOT.md.

1. Build the shards from the C1 results. Each shard holds at most 25 trajectories and 20 MB. The
   shards together hold the top 60 per domain, ranked by the sum of the two highest axis scores.

   ```bash
   uv run steward c2-handoff
   uv run steward validate c2_input data/handoff/c2/shard_000.jsonl
   ```

2. Commit the shards on handoff/c2 and push them. The branch continues from its remote tip,
   with main merged in, so history is never rewritten.

   ```bash
   uv run steward c2-push
   ```

3. Start the cloud sessions. `c2-launch` starts sessions for shards that have not been launched
   yet, keeping at most six running. A session counts as running until its results branch
   exists. Re-run the command as sessions finish. Launches are logged to
   data/handoff/c2_launches.jsonl, together with each session URL.

   ```bash
   uv run steward c2-launch
   ```

   `claude --cloud` needs a terminal, so the launcher runs it under `script`.

4. When the results branches exist, fetch them, merge, validate, rank and analyze.

   ```bash
   uv run steward c2-fetch
   uv run steward c2-merge
   uv run steward validate c2_merged data/stage_c2/results.jsonl
   uv run steward rank && uv run steward analyze
   ```
