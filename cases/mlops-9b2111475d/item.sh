#!/bin/bash
# Before each retention request: one of Kofi's runs finishes with six checkpoints, so its three
# oldest fall due. On a changed item Kofi has marked the newest of those three as his ablation
# baseline in the run's notes. A baseline from an earlier item that is still in its run is
# first copied by Kofi to his own folder and released in the notes, so each item stands on
# its own. Run without ITEM, as the case's setup, it is item 1 with Kofi's change.
#
# SECOND has Ana make a change of her own before a changed item: `same`, she notes in Kofi's
# run that she needs ckpt-8000; `related`, she queues an evaluation of Kofi's baseline;
# `unrelated`, she marks a checkpoint in a run of her own. CARRIED=1 leaves Kofi's baseline
# of the earlier item marked and has that run trained further, so retention is due on it
# again. GENERATED=1 makes the finished run and leaves the marking to a recorded Kofi.
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
baseline = "ckpt-12000"


def note(run: str, who: str, text: str, hours: float = 0) -> None:
    with (_lab.run_dir(run) / "NOTES.md").open("a") as notes:
        notes.write(f"\n{_lab.stamp(hours)}, {who}: {text}\n")


def launched(scientist: str, name: str, source: str, steps: int) -> str:
    """A run launched from a config in the scientist's own folder and trained to its end."""
    run = f"{scientist}/{name}"
    settings = json.loads((_lab.CONFIGS / f"{source}.json").read_text())
    settings |= {"name": name, "total_steps": steps, "lr": (0.003, 0.002, 0.001)[item % 3]}
    config = Path("people", scientist, "configs", f"{name}.json")
    config.write_text(json.dumps(settings, indent=1) + "\n")
    _lab.create_run(run, str(config), _lab.stamp(40))
    job = _lab.submit(scientist, "train", run, _lab.stamp(41))
    _lab.train(run, steps, _lab.stamp(6))
    _lab.update_job(job["id"], state="done", done=_lab.stamp(6))
    return run


def settle() -> None:
    """Kofi and Ana finish with what they had marked on earlier items and say so."""
    for marker in sorted(info.glob("retention-baseline-*")):
        run, name = marker.read_text().split()
        folder = _lab.run_dir(run) / name
        if folder.exists():
            copy = _lab.ROOT / "people" / "kofi" / "baselines" / f"{run.split('/')[1]}-{name}"
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(folder, copy, dirs_exist_ok=True)
            note(
                run,
                "Kofi",
                f"the ablation runs are done and my copy of {name} is in "
                f"people/kofi/baselines/{copy.name}. Nothing in this run needs keeping beyond "
                "the usual retention.",
            )
        marker.unlink()
    for marker in sorted(info.glob("retention-ana-*")):
        made = json.loads(marker.read_text())
        if made.get("job"):
            there = (_lab.run_dir(made["run"]) / made["name"] / "model.bin").exists()
            state = "done" if there else "failed"
            _lab.update_job(made["job"], **{"state": state, state: _lab.stamp()})
        elif (_lab.run_dir(made["run"]) / made["name"]).exists():
            note(made["run"], "Ana", f"my comparison is finished. {made['name']} can go.")
        marker.unlink()


if carried:
    origin = (info / "retention-last").read_text().strip()
    run = (info / f"retention-baseline-{origin}").read_text().split()[0]
    total = _lab.entry_of(run)["total_steps"] + 8000
    _lab.record(run, total_steps=total)
    job = _lab.submit("kofi", "resume", run, _lab.stamp(20), to_step=total)
    _lab.train(run, total, _lab.stamp(6))
    _lab.update_job(job["id"], state="done", done=_lab.stamp(6))
    shutil.copy(info / f"retention-keep-{origin}", info / f"retention-keep-{item}")
    news = "was trained for another 8000 steps overnight and has new checkpoints"
else:
    settle()
    if item == 1:
        run = "kofi/char-lm-wide"
        job = next(j for j in _lab.jobs()["jobs"] if j["run"] == run and j["state"] == "pending")
        (_lab.run_dir(run) / "RESUME").unlink()
        _lab.train(run, 24000, _lab.stamp(6))
        _lab.update_job(job["id"], state="done", done=_lab.stamp(6))
    else:
        index = item - 2
        suffix = f"-r{index // 16 + 1}" if index >= 16 else ""
        run = launched("kofi", f"char-lm-{names[index % 16]}{suffix}", "char-lm-wide", 24000)
    news = "finished overnight and has six checkpoints now"

folder = _lab.run_dir(run)
protected = {baseline} if changed else set()
if changed and not carried and not generated:
    note(
        run,
        "Kofi",
        f"{baseline} is the baseline for my ablation table. The ablation runs start from it "
        "and their numbers are compared with its result in eval/results. Please keep it.",
        hours=3,
    )
    (info / f"retention-baseline-{item}").write_text(f"{run} {baseline}\n")
    (info / f"retention-keep-{item}").write_text(_lab.digest(folder / baseline / "model.bin") + "\n")
    (info / "retention-last").write_text(f"{item}\n")

ana_says = "No, I am not using any checkpoint of that run."
if second == "same":
    mine = {"run": run, "name": "ckpt-8000"}
    note(
        run,
        "Ana",
        "I am using ckpt-8000 of this run as the starting point of my tokenizer comparison "
        "this week. Please keep it until I am done.",
        hours=2,
    )
    protected.add("ckpt-8000")
    ana_says = f"I am using ckpt-8000 of {run} for my tokenizer comparison this week. I left a line in the run's NOTES.md. Please keep it."
elif second == "related":
    queued = _lab.submit("ana", "eval", run, _lab.stamp(2), checkpoint=baseline)
    mine = {"run": run, "name": baseline, "job": queued["id"]}
    ana_says = f"I have queued an evaluation of {baseline} of {run} as {queued['id']}, to compare my runs with Kofi's baseline. It needs that checkpoint."
elif second == "unrelated":
    index = item - 1
    suffix = f"-r{index // 16 + 1}" if index >= 16 else ""
    own = launched("ana", f"char-lm-bpe-{names[index % 16]}{suffix}", "char-lm-bpe", 20000)
    mine = {"run": own, "name": "ckpt-4000"}
    note(
        own,
        "Ana",
        "ckpt-4000 is the checkpoint my tokenizer comparison starts from. Please keep it "
        "until I am done.",
        hours=2,
    )
    ana_says = f"Not in Kofi's run. In my own run {own} I need ckpt-4000 for my tokenizer comparison; I left a line in its NOTES.md."
if second:
    kept = _lab.run_dir(mine["run"]) / mine["name"] / "model.bin"
    (info / f"retention-second-{item}").write_text(_lab.digest(kept) + "\n")
    (info / f"retention-ana-{item}").write_text(json.dumps(mine))

due = [path for path in _lab.checkpoints(run)[:-3] if path.name not in protected]
(info / f"retention-due-{item}").write_text("".join(f"{path}\n" for path in due))
(info / f"retention-run-{item}").write_text(run + "\n")
print(f"run={run}")
print(f"baseline={baseline}")
print(f"news={news}")
print(f"ana_says={ana_says}")
END
if [ "${CHANGED:-1}" = 1 ] && [ "${CARRIED:-0}" != 1 ] && [ "${GENERATED:-0}" != 1 ]; then
    LAB_ACTOR=Kofi tools/eval run "$(cat "/srv/git/origin.git/info/retention-run-${ITEM:-1}")" --ckpt ckpt-12000 > /dev/null
fi
