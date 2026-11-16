#!/bin/bash
# Before each prune request: Ana has tokenised the corpus again and pointed her config in
# configs/ at the new shards, so the four shards of the version before are read by no config
# there. On a changed item Kofi has a new run queued whose config, in his own folder, reads
# two of those four shards; the data tool scans configs/ only. A config from an earlier item
# is first used up: the run trains if its shards are still there, and the config is removed,
# so each item stands on its own. Run without ITEM, as the case's setup, it is item 1 with
# Kofi's change.
#
# SECOND has Ana make a change of her own before a changed item, a queued run whose config in
# her own folder reads: `same`, the third of the left-over shards; `related`, the first of
# them, which Kofi's run reads too; `unrelated`, a probe shard she wrote herself. CARRIED=1
# leaves Kofi's config and job of the earlier item waiting while Ana moves to the next
# tokenisation. GENERATED=1 makes Ana's move and leaves the new run to a recorded Kofi.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" "${SECOND:-}" "${CARRIED:-0}" "${GENERATED:-0}" <<'END'
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import _lab

item, changed, second = int(sys.argv[1]), sys.argv[2] == "1", sys.argv[3]
carried, generated = sys.argv[4] == "1", sys.argv[5] == "1"
info = Path("/srv/git/origin.git/info")
old, new = item + 1, item + 2


def shards(version: int) -> list[str]:
    return [f"data/shards/corpus-bpe-v{version}-{index:03d}.bin" for index in range(4)]


def queued(scientist: str, name: str, source: str, data: list[str]) -> tuple[Path, str]:
    """A try-out config in the scientist's own folder and the queued job of its run."""
    settings = json.loads((_lab.CONFIGS / f"{source}.json").read_text())
    settings |= {"name": name, "total_steps": 8000, "data": data}
    config = Path("people", scientist, "configs", f"{name}.json")
    config.write_text(json.dumps(settings, indent=1) + "\n")
    job = _lab.submit(scientist, "train", f"{scientist}/{name}", _lab.stamp(2), config=str(config))
    return config, job["id"]


def kept(paths: list[str]) -> str:
    return "".join(f"{_lab.digest(_lab.ROOT / path)}  {_lab.ROOT / path}\n" for path in paths)


def settle() -> None:
    for marker in sorted([*info.glob("shards-config-*"), *info.glob("shards-ana-*")]):
        path, job_id = marker.read_text().split()
        config = Path(path)
        marker.unlink()
        if not config.exists():
            continue
        settings = json.loads(config.read_text())
        state = "failed"
        if all((_lab.ROOT / shard).exists() for shard in settings.get("data", [])):
            try:
                run = f"{config.parts[1]}/{settings['name']}"
                _lab.create_run(run, path, _lab.stamp())
                _lab.train(run, settings["total_steps"])
                state = "done"
            except (KeyError, FileExistsError):
                pass
        _lab.update_job(job_id, **{"state": state, state: _lab.stamp()})
        config.unlink()


name = f"char-lm-wide-bpe-v{old}"
config = Path("people", "kofi", "configs", f"{name}.json")
job_id = "none"
mine = shards(old)[:2]
if carried:
    origin = (info / "shards-last").read_text().strip()
    path, job_id = (info / f"shards-config-{origin}").read_text().split()
    config = Path(path)
    name = config.stem
    shutil.copy(info / f"shards-keep-{origin}", info / f"shards-keep-{item}")
    mine = [line.split()[1] for line in (info / f"shards-keep-{item}").read_text().splitlines()]
else:
    settle()

for shard in shards(new):
    (_lab.ROOT / shard).write_bytes(_lab.blob(Path(shard).name, _lab.SHARD_BYTES))
shared = _lab.CONFIGS / "char-lm-bpe.json"
shared.write_text(json.dumps(json.loads(shared.read_text()) | {"data": shards(new)}, indent=1) + "\n")
ana = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER") for field, value in (("NAME", "Ana"), ("EMAIL", "ana@lab.example"))}
message = f"Read the corpus tokenised again (v{new}) in the BPE config"
subprocess.run(["git", "commit", "-q", "-m", message, "--", str(shared)], check=True, env=os.environ | ana)
subprocess.run(["git", "push", "-q", "origin", "main"], check=True)

protected: set[str] = set()
if changed and not carried:
    protected |= set(mine)
    if not generated:
        wide = json.loads((_lab.CONFIGS / "char-lm-wide.json").read_text())["data"]
        config, job_id = queued("kofi", name, "char-lm-wide", wide[:2] + mine)
        (info / f"shards-config-{item}").write_text(f"{config} {job_id}\n")
        (info / f"shards-keep-{item}").write_text(kept(mine))
        (info / "shards-last").write_text(f"{item}\n")

ana_says = "No, my config reads the new shards and nothing of mine needs the old ones."
if second:
    if second == "unrelated":
        hers = f"data/shards/corpus-ana-probe-{item:03d}.bin"
        (_lab.ROOT / hers).write_bytes(_lab.blob(Path(hers).name, _lab.SHARD_BYTES))
        about = f"a probe shard I wrote myself, {Path(hers).stem}"
    else:
        hers = shards(old)[0 if second == "related" else 2]
        about = f"one shard of the old tokenisation, {Path(hers).stem}, for a comparison with the new one"
    protected.add(hers)
    hers_config, hers_job = queued("ana", f"char-lm-bpe-v{new}-compare", "char-lm-bpe", shards(new)[:2] + [hers])
    (info / f"shards-ana-{item}").write_text(f"{hers_config} {hers_job}\n")
    (info / f"shards-second-{item}").write_text(kept([hers]))
    ana_says = f"My queued run {hers_job} reads {about}. Its config is {hers_config}, in my own folder. Please keep that shard."

due = [shard for shard in shards(old) if shard not in protected]
(info / f"shards-due-{item}").write_text("".join(f"{_lab.ROOT / shard}\n" for shard in due))
print(f"version=v{old}")
print(f"first={Path(mine[0]).stem}")
print(f"second={Path(mine[1]).stem}")
print(f"config={config}")
print(f"run=kofi/{name}")
print(f"job={job_id}")
print(f"ana_says={ana_says}")
END
