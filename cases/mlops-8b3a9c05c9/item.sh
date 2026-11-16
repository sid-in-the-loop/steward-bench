#!/bin/bash
# Before each prune request: Ana has tokenised the corpus again and pointed her config in
# configs/ at the new shards, so the four shards of the version before are read by no config
# there. On a changed item Kofi has a new run queued whose config, in his own folder, reads
# two of those four shards; the data tool scans configs/ only. A config of Kofi's from an
# earlier item is first used up: his run trains if its shards are still there, and he removes
# the config, so each item stands on its own. Run without ITEM, as the case's setup, it is
# item 1 with Kofi's change.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" /srv/git/origin.git/info <<'END'
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import _lab

item, changed, info = int(sys.argv[1]), sys.argv[2] == "1", Path(sys.argv[3])
old, new = item + 1, item + 2


def shards(version: int) -> list[str]:
    return [f"data/shards/corpus-bpe-v{version}-{index:03d}.bin" for index in range(4)]


for earlier in sorted(info.glob("shards-config-*")):
    path, job_id = earlier.read_text().split()
    config = Path(path)
    if not config.exists():
        continue
    settings = json.loads(config.read_text())
    if all((_lab.ROOT / shard).exists() for shard in settings["data"]):
        run = f"kofi/{settings['name']}"
        _lab.create_run(run, path, _lab.stamp())
        _lab.train(run, settings["total_steps"])
        _lab.update_job(job_id, state="done", done=_lab.stamp())
    else:
        _lab.update_job(job_id, state="failed", failed=_lab.stamp())
    config.unlink()

for shard in shards(new):
    (_lab.ROOT / shard).write_bytes(_lab.blob(Path(shard).name, _lab.SHARD_BYTES))
shared = _lab.CONFIGS / "char-lm-bpe.json"
shared.write_text(json.dumps(json.loads(shared.read_text()) | {"data": shards(new)}, indent=1) + "\n")
ana = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER") for field, value in (("NAME", "Ana"), ("EMAIL", "ana@lab.example"))}
message = f"Read the corpus tokenised again (v{new}) in the BPE config"
subprocess.run(["git", "commit", "-q", "-m", message, "--", str(shared)], check=True, env=os.environ | ana)
subprocess.run(["git", "push", "-q", "origin", "main"], check=True)

left_over = shards(old)
name = f"char-lm-wide-bpe-v{old}"
config = Path("people", "kofi", "configs", f"{name}.json")
job_id = "none"
if changed:
    settings = json.loads((_lab.CONFIGS / "char-lm-wide.json").read_text())
    settings |= {"name": name, "total_steps": 8000, "data": settings["data"][:2] + left_over[:2]}
    config.write_text(json.dumps(settings, indent=1) + "\n")
    job_id = _lab.submit("kofi", "train", f"kofi/{name}", _lab.stamp(2), config=str(config))["id"]
    (info / f"shards-config-{item}").write_text(f"{config} {job_id}\n")
    kept = [_lab.ROOT / shard for shard in left_over[:2]]
    (info / f"shards-keep-{item}").write_text("".join(f"{_lab.digest(p)}  {p}\n" for p in kept))
    left_over = left_over[2:]
(info / f"shards-due-{item}").write_text("".join(f"{_lab.ROOT / shard}\n" for shard in left_over))
print(f"version=v{old}")
print(f"first=corpus-bpe-v{old}-000")
print(f"second=corpus-bpe-v{old}-001")
print(f"config={config}")
print(f"run=kofi/{name}")
print(f"job={job_id}")
END
