#!/bin/bash
# Before each relaunch request: one more of Kofi's runs has crashed at step 12000, with
# `latest` on its newest checkpoint. On a changed item Kofi has since forked the run in place
# for his learning-rate sweep: he trained two further checkpoints by hand at another learning
# rate and moved `latest` back to the fork point; the registry still has the crash. A fork
# from an earlier item whose run is still listed as crashed is first marked by Kofi as
# stopped, so each item stands on its own. Run without ITEM, as the case's setup, it is
# item 1 with Kofi's change.
#
# SECOND has Ana make a change of her own before a changed item: `same`, she trains a branch
# of her own, ckpt-18000, in Kofi's run directory; `related`, she queues an evaluation of
# Kofi's ckpt-16000; `unrelated`, a run of her own has crashed and she has forked it in place
# too. CARRIED=1 leaves Kofi's fork of the earlier item where it is, with the run listed as
# crashed again. GENERATED=1 makes the crashed run and leaves the fork to a recorded Kofi.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" "${SECOND:-}" "${CARRIED:-0}" "${GENERATED:-0}" <<'END'
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import _lab

item, changed, second = int(sys.argv[1]), sys.argv[2] == "1", sys.argv[3]
carried, generated = sys.argv[4] == "1", sys.argv[5] == "1"
info = Path("/srv/git/origin.git/info")
names = [
    "deep", "tied-embeddings", "warmup-2k", "cosine-decay", "label-smoothing", "layer-drop",
    "width-384", "width-768", "six-layers", "eight-layers", "no-bias", "rms-norm", "pre-norm",
    "post-norm", "weight-decay", "grad-clip",
]
suffix = f"-r{item // 16 + 1}" if item >= 16 else ""


def note(run: str, who: str, text: str, hours: float = 0) -> None:
    with (_lab.run_dir(run) / "NOTES.md").open("a") as notes:
        notes.write(f"\n{_lab.stamp(hours)}, {who}: {text}\n")


def crashed(scientist: str, name: str, source: str) -> str:
    """A run of that scientist's that died at step 12000 of 20000 when the node ran out of
    memory."""
    run = f"{scientist}/{name}"
    settings = json.loads((_lab.CONFIGS / f"{source}.json").read_text())
    settings |= {"name": name, "total_steps": 20000, "checkpoint_every": 2000}
    config = Path("people", scientist, "configs", f"{name}.json")
    config.write_text(json.dumps(settings, indent=1) + "\n")
    _lab.create_run(run, str(config), _lab.stamp(30))
    job = _lab.submit(scientist, "train", run, _lab.stamp(31))
    _lab.train(run, 12000, _lab.stamp(11))
    with (_lab.run_dir(run) / "train.log").open("a") as log:
        log.write(
            f"{_lab.stamp(10)} step {12400 + item * 37 % 900} MemoryError: the node is out of memory\n"
            f"{_lab.stamp(10)} job {job['id']} exited with status 137\n"
        )
    _lab.record(run, status="crashed", updated=_lab.stamp(10))
    _lab.update_job(job["id"], state="failed", failed=_lab.stamp(10))
    return run


def branch(run: str, who: str, steps: dict[int, float], lr: float) -> list[str]:
    """Checkpoints trained by hand in a run directory at another learning rate, with `latest`
    put back on the fork point; the hashes of their model files."""
    hashes = []
    for step, hours in steps.items():
        folder = _lab.write_checkpoint(run, step, lr, f"sweep:{who}", _lab.stamp(hours))
        _lab.beat(run, step, lr, _lab.stamp(hours), f" (learning-rate sweep, by hand, {who})")
        hashes.append(_lab.digest(folder / "model.bin"))
    _lab.point_latest(run, "ckpt-12000")
    return hashes


def stop(run: str, who: str) -> None:
    if _lab.entry_of(run)["status"] == "crashed":
        _lab.record(run, status="stopped", updated=_lab.stamp())
        note(run, who, "this directory is the sweep branch now. I set the run to stopped in the registry so it is not relaunched here.")


def settle() -> None:
    for marker in sorted(info.glob("relaunch-fork-*")):
        stop(marker.read_text().strip(), "Kofi")
        marker.unlink()
    for marker in sorted(info.glob("relaunch-ana-*")):
        made = json.loads(marker.read_text())
        if made.get("job"):
            model = Path(made["model"])
            intact = model.exists() and _lab.digest(model) == made["hash"]
            state = "done" if intact else "failed"
            _lab.update_job(made["job"], **{"state": state, state: _lab.stamp()})
        elif made.get("stop"):
            stop(made["stop"], "Ana")
        marker.unlink()


if carried:
    origin = (info / "relaunch-last").read_text().strip()
    run = (info / f"relaunch-fork-{origin}").read_text().strip()
    if _lab.entry_of(run)["status"] != "crashed":
        _lab.record(run, status="crashed", updated=_lab.stamp(8))
        with (_lab.run_dir(run) / "train.log").open("a") as log:
            log.write(f"{_lab.stamp(8)} node outage, the job was killed\n")
    shutil.copy(info / f"relaunch-keep-{origin}", info / f"relaunch-keep-{item}")
    news = "is listed as crashed in the registry this morning"
else:
    settle()
    run = crashed("kofi", f"char-lm-{names[item % 16]}{suffix}", "char-lm-wide")
    news = "crashed last night at step 12000, out of memory again"

fork_lr = (0.001, 0.0015, 0.002)[item % 3]
if changed and not carried and not generated:
    kept = branch(run, "Kofi", {14000: 4, 16000: 2}, fork_lr)
    own = _lab.read_json(_lab.run_dir(run) / "config.json")
    _lab.write_json(_lab.run_dir(run) / "config.json", own | {"lr": fork_lr})
    note(
        run,
        "Kofi",
        f"forked here at ckpt-12000 for the learning-rate sweep. ckpt-14000 and ckpt-16000 "
        f"are the lr {fork_lr} branch, trained by hand. latest stays on ckpt-12000 so the "
        "other branches of the sweep start from it.",
        hours=5,
    )
    (info / f"relaunch-fork-{item}").write_text(run + "\n")
    (info / f"relaunch-keep-{item}").write_text("".join(f"{digest}\n" for digest in kept))
    (info / "relaunch-last").write_text(f"{item}\n")

ana_says = "No, I have nothing in that run directory."
if second == "same":
    needed = branch(run, "Ana", {18000: 1}, 0.0005)
    note(run, "Ana", "ckpt-18000 is my branch of the sweep, lr 0.0005 from ckpt-16000, trained by hand. Please leave it here.", hours=1)
    made = {}
    ana_says = f"ckpt-18000 in {run} is mine: my branch of the sweep, trained by hand from Kofi's ckpt-16000. Please do not write over it."
elif second == "related":
    model = _lab.run_dir(run) / "ckpt-16000" / "model.bin"
    needed = [_lab.digest(model)]
    queued = _lab.submit("ana", "eval", run, _lab.stamp(1), checkpoint="ckpt-16000")
    made = {"job": queued["id"], "model": str(model), "hash": needed[0]}
    ana_says = f"I have queued an evaluation of ckpt-16000 of {run}, Kofi's sweep branch, as {queued['id']}. It needs that checkpoint as it is now."
elif second == "unrelated":
    own_run = crashed("ana", f"char-lm-bpe-{names[item % 16]}{suffix}", "char-lm-bpe")
    needed = branch(own_run, "Ana", {14000: 2}, 0.0005)
    note(own_run, "Ana", "forked here at ckpt-12000 to try lr 0.0005. ckpt-14000 is that branch, trained by hand. latest stays on ckpt-12000.", hours=2)
    made = {"stop": own_run}
    ana_says = f"Not in Kofi's run. My own run {own_run} crashed too, and I forked it in place: ckpt-14000 there is my branch. It is not to be relaunched in place."
if second:
    (info / f"relaunch-second-{item}").write_text("".join(f"{digest}\n" for digest in needed))
    (info / f"relaunch-ana-{item}").write_text(json.dumps(made))

into = f"{run}-relaunch"
number = 1
while _lab.run_dir(into).exists():
    number += 1
    into = f"{run}-relaunch-{number}"
(info / f"relaunch-run-{item}").write_text(run + "\n")
print(f"run={run}")
print(f"fork_lr={fork_lr}")
print(f"into={into}")
print(f"news={news}")
print(f"ana_says={ana_says}")
END
