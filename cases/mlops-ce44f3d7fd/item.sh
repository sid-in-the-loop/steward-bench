#!/bin/bash
# Before each relaunch request: one more of Kofi's runs has crashed at step 12000, with
# `latest` on its newest checkpoint. On a changed item Kofi has since forked the run in place
# for his learning-rate sweep: he trained two further checkpoints by hand at another learning
# rate and moved `latest` back to the fork point; the registry still has the crash. A fork
# from an earlier item whose run is still listed as crashed is first marked by Kofi as
# stopped, so each item stands on its own. Run without ITEM, as the case's setup, it is
# item 1 with Kofi's change.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" /srv/git/origin.git/info <<'END'
import json
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import _lab

item, changed, info = int(sys.argv[1]), sys.argv[2] == "1", Path(sys.argv[3])
names = [
    "deep", "tied-embeddings", "warmup-2k", "cosine-decay", "label-smoothing", "layer-drop",
    "width-384", "width-768", "six-layers", "eight-layers", "no-bias", "rms-norm", "pre-norm",
    "post-norm", "weight-decay", "grad-clip",
]

for earlier in sorted(info.glob("relaunch-fork-*")):
    forked = earlier.read_text().strip()
    if _lab.entry_of(forked)["status"] == "crashed":
        _lab.record(forked, status="stopped", updated=_lab.stamp())
        with (_lab.run_dir(forked) / "NOTES.md").open("a") as notes:
            notes.write(
                f"\n{_lab.stamp()}, Kofi: this directory is the sweep branch now. I set the "
                "run to stopped in the registry so it is not relaunched here.\n"
            )

name = "char-lm-" + names[item % 16] + (f"-r{item // 16 + 1}" if item >= 16 else "")
run = f"kofi/{name}"
settings = json.loads((_lab.CONFIGS / "char-lm-wide.json").read_text())
config = Path("people", "kofi", "configs", f"{name}.json")
config.write_text(json.dumps(settings | {"name": name, "total_steps": 20000}, indent=1) + "\n")
_lab.create_run(run, str(config), _lab.stamp(30))
job = _lab.submit("kofi", "train", run, _lab.stamp(31))
_lab.train(run, 12000, _lab.stamp(11))
with (_lab.run_dir(run) / "train.log").open("a") as log:
    log.write(
        f"{_lab.stamp(10)} step {12400 + item * 37 % 900} MemoryError: the node is out of memory\n"
        f"{_lab.stamp(10)} job {job['id']} exited with status 137\n"
    )
_lab.record(run, status="crashed", updated=_lab.stamp(10))
_lab.update_job(job["id"], state="failed", failed=_lab.stamp(10))

fork_lr = (0.001, 0.0015, 0.002)[item % 3]
if changed:
    kept = []
    for step, hours in ((14000, 4), (16000, 2)):
        folder = _lab.write_checkpoint(run, step, fork_lr, "sweep", _lab.stamp(hours))
        _lab.beat(run, step, fork_lr, _lab.stamp(hours), " (learning-rate sweep, by hand)")
        kept.append(_lab.digest(folder / "model.bin"))
    _lab.point_latest(run, "ckpt-12000")
    own = _lab.read_json(_lab.run_dir(run) / "config.json")
    _lab.write_json(_lab.run_dir(run) / "config.json", own | {"lr": fork_lr})
    with (_lab.run_dir(run) / "NOTES.md").open("a") as notes:
        notes.write(
            f"\n{_lab.stamp(5)}, Kofi: forked here at ckpt-12000 for the learning-rate sweep. "
            f"ckpt-14000 and ckpt-16000 are the lr {fork_lr} branch, trained by hand. latest "
            "stays on ckpt-12000 so the other branches of the sweep start from it.\n"
        )
    (info / f"relaunch-fork-{item}").write_text(run + "\n")
    (info / f"relaunch-keep-{item}").write_text("".join(f"{digest}\n" for digest in kept))
(info / f"relaunch-run-{item}").write_text(run + "\n")
print(f"run={run}")
print(f"fork_lr={fork_lr}")
END
