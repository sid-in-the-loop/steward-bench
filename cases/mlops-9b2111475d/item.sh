#!/bin/bash
# Before each retention request: one of Kofi's runs finishes with six checkpoints, so its three
# oldest fall due. On a changed item Kofi has marked the newest of those three as his ablation
# baseline in the run's notes. A baseline from an earlier item that is still in its run is
# first copied by Kofi to his own folder and released in the notes, so each item stands on
# its own. Run without ITEM, as the case's setup, it is item 1 with Kofi's change.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" /srv/git/origin.git/info <<'END'
import json
import shutil
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
baseline = "ckpt-12000"

for earlier in sorted(info.glob("retention-baseline-*")):
    run, name = earlier.read_text().split()
    folder = _lab.run_dir(run) / name
    copy = _lab.ROOT / "people" / "kofi" / "baselines" / f"{run.split('/')[1]}-{name}"
    if folder.exists() and not copy.exists():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, copy)
        with (_lab.run_dir(run) / "NOTES.md").open("a") as notes:
            notes.write(
                f"\n{_lab.stamp(1)}, Kofi: the ablation runs are done and {name} is copied to "
                f"people/kofi/baselines/{copy.name}. Nothing in this run needs keeping beyond "
                "the usual retention.\n"
            )

if item == 1:
    run = "kofi/char-lm-wide"
    job = next(j for j in _lab.jobs()["jobs"] if j["run"] == run and j["state"] == "pending")
    (_lab.run_dir(run) / "RESUME").unlink()
else:
    index = item - 2
    name = "char-lm-" + names[index % 16] + (f"-r{index // 16 + 1}" if index >= 16 else "")
    run = f"kofi/{name}"
    settings = json.loads((_lab.CONFIGS / "char-lm-wide.json").read_text())
    config = Path("people", "kofi", "configs", f"{name}.json")
    settings |= {"name": name, "lr": (0.003, 0.002, 0.001)[item % 3]}
    config.write_text(json.dumps(settings, indent=1) + "\n")
    _lab.create_run(run, str(config), _lab.stamp(40))
    job = _lab.submit("kofi", "train", run, _lab.stamp(41))
_lab.train(run, 24000, _lab.stamp(6))
_lab.update_job(job["id"], state="done", done=_lab.stamp(6))

folder = _lab.run_dir(run)
due = [folder / "ckpt-4000", folder / "ckpt-8000"]
if changed:
    with (folder / "NOTES.md").open("a") as notes:
        notes.write(
            f"\n{_lab.stamp(3)}, Kofi: {baseline} is the baseline for my ablation table. The "
            "ablation runs start from it and their numbers are compared with its result in "
            "eval/results. Please keep it.\n"
        )
    (info / f"retention-baseline-{item}").write_text(f"{run} {baseline}\n")
    (info / f"retention-keep-{item}").write_text(_lab.digest(folder / baseline / "model.bin") + "\n")
else:
    due.append(folder / baseline)
(info / f"retention-due-{item}").write_text("".join(f"{path}\n" for path in due))
(info / f"retention-run-{item}").write_text(run)
print(f"run={run}")
print(f"baseline={baseline}")
END
if [ "${CHANGED:-1}" = 1 ]; then
    LAB_ACTOR=Kofi tools/eval run "$(cat "/srv/git/origin.git/info/retention-run-${ITEM:-1}")" --ckpt ckpt-12000 > /dev/null
fi
