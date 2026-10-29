# Stage C2 in a cloud session

This is the exact procedure a cloud session started with `claude --cloud` follows for one shard.
It also covers the cluster side: building the shards, starting the sessions and merging the
results. Brief sections 3.2 and 6.5 define the stage.

The brief requires a private repository, because shards carry windowed renderings of gated data
(brief 4.7). For this run the owner waived that requirement (reports/DECISIONS.md). Renderings
are passed through secret redaction (steward/filter/redact.py) before shards are written.

## Inside the cloud session (one shard)

The session is started from the repository root with a prompt like:

```bash
claude --cloud "Stage C2 for data/handoff/c2/shard_007.jsonl on branch handoff/c2. \
Follow docs/stage_c2_cloud.md exactly. Push results to branch results/c2-shard-007."
```

Follow these steps in order. Do not change prompts, schemas or code.

1. Check out the handoff branch and install the package.

   ```bash
   git fetch origin handoff/c2 && git checkout handoff/c2
   pip install uv && uv sync
   ```

2. Split the shard into one input file per trajectory, and print the result schema.

   ```bash
   uv run steward c2-split data/handoff/c2/shard_007.jsonl /tmp/c2/shard_007
   uv run steward c2-schema > /tmp/c2/schema.json
   ```

3. For each input file, start one subagent on the opus model. Give it exactly three things: the
   text of prompts/stage_c2.md, the JSON schema from /tmp/c2/schema.json, and that one input file.
   The input file holds the trajectory_id, the C1 result and the windowed rendering. Tell it to
   return only one JSON object matching the schema, with trajectory_id copied from the input.
   Each subagent sees one trajectory only. Run at most five subagents at a time.

4. Append each returned JSON object as one line to data/results/c2/shard_007.jsonl. Write only the
   object, without code fences.

5. Validate against the shard.

   ```bash
   uv run python -m steward.filter.validate c2 data/results/c2/shard_007.jsonl \
     --shard data/handoff/c2/shard_007.jsonl
   ```

   The validator checks the schema, that every score of 2 or 3 cites a step and an excerpt, that
   excerpts appear verbatim in the rendering, that every trajectory in the shard has a result,
   and that no trajectory_id is unknown or repeated.

6. For each trajectory the validator reports, delete its line and start one new subagent for it,
   with the validator's error message added to the instructions. Re-run each failure once only.
   Then validate again. Lines that still fail stay out of the file.

7. Commit only the results file and push it to the results branch.

   ```bash
   git checkout -b results/c2-shard-007
   git add -f data/results/c2/shard_007.jsonl
   git commit -m "Add Stage C2 results for shard 007"
   git push origin results/c2-shard-007
   ```

8. Reply with one line: the shard, results written, validation errors remaining.

The session must not write titles, names or descriptions of benchmark scenarios, must not edit
the handoff branch, and must not push anything other than the results file.

## On the cluster

1. Build the shards from C1 results. Each shard holds at most 25 trajectories and 20 MB, and the
   shards together hold the top 60 per domain by the sum of the two highest axis scores.

   ```bash
   uv run steward c2-handoff
   uv run steward validate c2_input data/handoff/c2/shard_000.jsonl
   ```

2. Commit the shards on the handoff branch and push. Only data/handoff goes on this branch.

   ```bash
   git checkout -b handoff/c2
   git add -f data/handoff/c2/shard_*.jsonl
   git commit -m "Add Stage C2 handoff shards"
   git push origin handoff/c2
   git checkout main
   ```

3. Start one cloud session per shard, with at most six running at a time, using the command at the
   top of this file with the shard number substituted.

4. When the sessions finish, fetch each results branch and copy its file into place.

   ```bash
   git fetch origin 'refs/heads/results/c2-shard-*:refs/remotes/origin/results/c2-shard-*'
   for ref in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin/results/); do
     n=${ref##*-}
     git show "$ref:data/results/c2/shard_$n.jsonl" > "data/results/c2/shard_$n.jsonl"
   done
   uv run steward c2-merge
   uv run steward validate c2_merged data/stage_c2/results.jsonl
   uv run steward rank && uv run steward analyze
   ```

The pilot uses the same procedure on a single shard built with `uv run steward c2-handoff --pilot`, which writes data/handoff/c2_pilot/shard_000.jsonl; its results go to data/results/c2_pilot/shard_000.jsonl on branch results/c2-pilot-shard-000 and merge with `uv run steward c2-merge --pilot`, after which `uv run steward pilot-report` writes reports/PILOT.md.
