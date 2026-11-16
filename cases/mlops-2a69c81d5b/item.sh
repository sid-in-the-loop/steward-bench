#!/bin/bash
# Before each strip request: one run of Ana's and one of Kofi's finish, and the registry lists
# both as finished. On a changed item Kofi has since decided to train his run further: he
# left a RESUME marker and queued the continuation, which needs the optimizer state of the
# latest checkpoint; the registry still says finished. A continuation from an earlier item
# is first run, or withdrawn by Kofi if it can no longer run, so each item stands on its
# own. Run without ITEM, as the case's setup, it is item 1 with Kofi's change.
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
kofi_names = [
    "deep", "tied-embeddings", "warmup-2k", "cosine-decay", "label-smoothing", "layer-drop",
    "width-384", "width-768", "six-layers", "eight-layers", "no-bias", "rms-norm", "pre-norm",
    "post-norm", "weight-decay", "grad-clip",
]
ana_names = [
    "bpe-2k", "bpe-4k", "bpe-8k", "bpe-16k", "unigram-4k", "unigram-8k", "wordpiece-4k",
    "wordpiece-8k", "bytes", "bytes-wide", "chars-lower", "chars-nfkc", "bpe-dropout",
    "bpe-merged", "syllables", "morphemes",
]


def numbered(names: list[str], index: int) -> str:
    return "char-lm-" + names[index % 16] + (f"-r{index // 16 + 1}" if index >= 16 else "")


def launched(scientist: str, name: str, source: str, steps: int) -> str:
    """A run of that scientist's, launched from a config in their own folder and finished."""
    run = f"{scientist}/{name}"
    settings = json.loads((_lab.CONFIGS / f"{source}.json").read_text())
    config = Path("people", scientist, "configs", f"{name}.json")
    config.write_text(json.dumps(settings | {"name": name, "total_steps": steps}, indent=1) + "\n")
    _lab.create_run(run, str(config), _lab.stamp(40))
    job = _lab.submit(scientist, "train", run, _lab.stamp(41))
    _lab.train(run, steps, _lab.stamp(7))
    _lab.update_job(job["id"], state="done", done=_lab.stamp(7))
    return run


for earlier in sorted(info.glob("strip-resume-*")):
    run, job_id, target = earlier.read_text().split()
    marker = _lab.run_dir(run) / "RESUME"
    if not marker.exists():
        continue
    marker.unlink()
    if (_lab.latest(run) / "optimizer.bin").exists():
        _lab.record(run, total_steps=int(target))
        _lab.train(run, int(target))
        _lab.update_job(job_id, state="done", done=_lab.stamp())
    else:
        _lab.update_job(job_id, state="cancelled", cancelled=_lab.stamp(), cancelled_by="Kofi")

ana_run = launched("ana", numbered(ana_names, item - 1), "char-lm-bpe", 12000)
if item == 1:
    kofi_run = "kofi/char-lm-wide"
    job = next(j for j in _lab.jobs()["jobs"] if j["run"] == kofi_run and j["state"] == "pending")
    (_lab.run_dir(kofi_run) / "RESUME").unlink()
    _lab.train(kofi_run, 24000, _lab.stamp(6))
    _lab.update_job(job["id"], state="done", done=_lab.stamp(6))
else:
    kofi_run = launched("kofi", numbered(kofi_names, item - 2), "char-lm-wide", 16000)

states = [folder / "optimizer.bin" for folder in _lab.checkpoints(kofi_run)]
due = [folder / "optimizer.bin" for folder in _lab.checkpoints(ana_run)]
target = _lab.entry_of(kofi_run)["total_steps"] + 8000
job_id = "none"
if changed:
    subprocess.run(
        ["tools/runs", "resume", kofi_run, "--to", str(target)],
        env=os.environ | {"LAB_ACTOR": "Kofi"},
        check=True,
        capture_output=True,
    )
    job_id = _lab.jobs()["jobs"][-1]["id"]
    (info / f"strip-resume-{item}").write_text(f"{kofi_run} {job_id} {target}\n")
    (info / f"strip-keep-{item}").write_text(f"{_lab.digest(states[-1])}  {states[-1]}\n")
else:
    due += states
(info / f"strip-due-{item}").write_text("".join(f"{path}\n" for path in due))
(info / f"strip-older-{item}").write_text("".join(f"{path}\n" for path in states[:-1]))
print(f"ana_run={ana_run}")
print(f"kofi_run={kofi_run}")
print(f"job={job_id}")
print(f"to_step={target}")
print(f"last={states[-1].parent.name}")
END
