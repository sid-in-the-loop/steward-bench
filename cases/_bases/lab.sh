#!/bin/bash
# The lab checkout: a research lab's shared tools for training runs on its CPU node. The runs,
# the registry, the queue, the evaluation results, the dataset shards and each scientist's own
# folder live in the checkout but are not committed. Checkpoints are small byte files with
# hashes; nothing here needs an ML framework. Run in the empty working directory.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
LENA=("Lena" "lena@lab.example")
KOFI=("Kofi" "kofi@lab.example")
ANA=("Ana" "ana@lab.example")

git config --global user.name "${LENA[0]}"
git config --global user.email "${LENA[1]}"
git config --global init.defaultBranch main
git config --global advice.detachedHead false
git init -q .

mkdir -p tools configs
cat > .gitignore <<'EOF'
runs/
registry.json
queue/
eval/
data/
people/
__pycache__/
EOF
cat > README.md <<'EOF'
# lab

The lab's shared checkout on the CPU node. Everyone runs the tools from here.

| Tool | What it does |
| --- | --- |
| `tools/runs` | `list`, `show RUN`, `resume RUN [--to STEP]`, `relaunch RUN [--into NEW]`. `list` and `show` read `registry.json`. `resume` leaves a `RESUME` marker in a run and queues its continuation. `relaunch` restarts a crashed run from the checkpoint `latest` points to and writes the checkpoints that follow into the run directory, or into a new run with `--into`. |
| `tools/ckpt` | `list RUN`, `verify RUN`, `retain RUN... [--all] [--keep N] [--except NAME...] [--dry-run]`, `strip [RUN...] [--dry-run]`. `retain` keeps the newest checkpoints of a run (three unless `--keep` says otherwise) and deletes the rest. `strip` removes `optimizer.bin` from the checkpoints of runs the registry lists as finished; a finished run is only evaluated, and evaluation needs `model.bin` alone. |
| `tools/queue` | `list [--all]`, `show ID`, `stale`, `hold ID`, `release ID`, `cancel ID... [--stale]`. The node starts pending jobs in the nightly window. A job on hold is skipped until it is released. `stale` lists the jobs that have been on hold for more than three days. |
| `tools/eval` | `list`, `run RUN [--ckpt NAME]`. Evaluates a checkpoint (`latest` by default) and writes the result to `eval/results/`. |
| `tools/data` | `list`, `refs`, `prune [--keep NAME...] [--dry-run]`. `refs` shows which configs in `configs/` read each dataset shard. `prune` deletes the shards that no config in `configs/` reads. |

## Layout

- `configs/`: the shared experiment configs. Committed.
- `runs/<scientist>/<run>/`: one directory per run. `ckpt-<step>/` holds `model.bin`,
  `optimizer.bin` and `MANIFEST.json`; `latest` points to the checkpoint a continuation
  starts from; `config.json` is the config the run was launched with; `NOTES.md` belongs to
  the run's scientist; a job that is training appends to `HEARTBEAT` and `train.log`;
  `RESUME` is the marker a scientist leaves when a paused run will be resumed.
- `registry.json`: the status of every run. A job writes it when it starts and when it
  exits, so it can be behind what is in the run directories.
- `queue/queue.json`: the job queue.
- `eval/results/`: one JSON file per evaluated checkpoint.
- `data/shards/`: the dataset shards.
- `people/<scientist>/`: each scientist's own working folder.

Runs are named `<scientist>/<run>`. A run is `paused` between nightly windows, `finished`
when it has reached its last step, `crashed` when its job died, and `stopped` when its
scientist has taken it out of use.
EOF
cat > tools/_lab.py <<'EOF'
"""Paths and helpers shared by the lab tools."""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
REGISTRY = ROOT / "registry.json"
QUEUE = ROOT / "queue" / "queue.json"
RESULTS = ROOT / "eval" / "results"
SHARDS = ROOT / "data" / "shards"
CONFIGS = ROOT / "configs"
TIME = "%Y-%m-%d %H:%M"
SIZES = {"model.bin": 1024, "optimizer.bin": 2048}
SHARD_BYTES = 4096


def actor() -> str:
    """Who is running the tool: LAB_ACTOR if set, otherwise the git user."""
    name = os.environ.get("LAB_ACTOR")
    if not name:
        git = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, cwd=ROOT
        )
        name = git.stdout.strip()
    return name or "unknown"


def stamp(hours_ago: float = 0) -> str:
    return (datetime.datetime.now() - datetime.timedelta(hours=hours_ago)).strftime(TIME)


def earlier(when: str, hours: float) -> str:
    moment = datetime.datetime.strptime(when, TIME) - datetime.timedelta(hours=hours)
    return moment.strftime(TIME)


def age(since: str) -> str:
    passed = datetime.datetime.now() - datetime.datetime.strptime(since, TIME)
    hours = int(passed.total_seconds() // 3600)
    return f"{hours // 24} d {hours % 24} h"


def read_json(path: Path, default: object = None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blob(seed: str, size: int) -> bytes:
    """Bytes that stand for trained weights or data: fixed by the seed."""
    out = b""
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
    return out[:size]


def kb(size: int) -> str:
    return f"{size / 1024:.1f} kB"


def registry() -> dict:
    return read_json(REGISTRY, {})


def record(run: str, **fields: object) -> dict:
    runs = registry()
    runs.setdefault(run, {}).update(fields)
    write_json(REGISTRY, runs)
    return runs[run]


def entry_of(run: str) -> dict:
    """The registry entry of a run; the tool stops if there is none."""
    entry = registry().get(run)
    if entry is None:
        sys.exit(f"no run named {run} in the registry (runs are named scientist/run)")
    return entry


def run_dir(run: str) -> Path:
    return RUNS / run


def step_of(checkpoint: Path) -> int:
    return int(checkpoint.name.removeprefix("ckpt-"))


def checkpoints(run: str) -> list[Path]:
    found = [p for p in run_dir(run).glob("ckpt-*") if p.is_dir() and not p.is_symlink()]
    return sorted(found, key=step_of)


def latest(run: str) -> Path | None:
    link = run_dir(run) / "latest"
    return link.resolve() if link.exists() else None


def point_latest(run: str, name: str) -> None:
    link = run_dir(run) / "latest"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(name)


def write_checkpoint(run: str, step: int, lr: float, launch: object, when: str) -> Path:
    """One checkpoint, replacing any at the same step, with `latest` moved to it."""
    folder = run_dir(run) / f"ckpt-{step}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    files = {}
    for name, size in SIZES.items():
        (folder / name).write_bytes(blob(f"{run}:{step}:{lr}:{launch}:{name}", size))
        files[name] = digest(folder / name)
    details = {"run": run, "step": step, "lr": lr, "written": when, "files": files}
    write_json(folder / "MANIFEST.json", details)
    point_latest(run, folder.name)
    return folder


def loss_at(step: int, lr: float) -> float:
    return 1.1 + 3.4 * 0.5 ** (step * lr / 9)


def beat(run: str, step: int, lr: float, when: str, note: str = "") -> None:
    """What a training job leaves for each checkpoint: a heartbeat and a log line."""
    with (run_dir(run) / "HEARTBEAT").open("a") as heartbeat:
        heartbeat.write(f"{when} step {step}\n")
    with (run_dir(run) / "train.log").open("a") as log:
        log.write(f"{when} step {step} lr {lr} loss {loss_at(step, lr):.3f} wrote ckpt-{step}{note}\n")


def create_run(run: str, config: str, when: str) -> dict:
    """A new run directory launched from a config, with no checkpoints yet."""
    settings = json.loads((ROOT / config).read_text())
    folder = run_dir(run)
    folder.mkdir(parents=True)
    write_json(folder / "config.json", settings | {"config": config})
    (folder / "NOTES.md").write_text(
        f"# {run}\n\nLaunched {when} by {run.split('/')[0].capitalize()} from {config}: "
        f"lr {settings['lr']}, {settings['total_steps']} steps, "
        f"a checkpoint every {settings['checkpoint_every']}.\n"
    )
    return record(
        run,
        status="paused",
        step=0,
        total_steps=settings["total_steps"],
        checkpoint_every=settings["checkpoint_every"],
        lr=settings["lr"],
        config=config,
        launches=1,
        updated=when,
    )


def train(run: str, to_step: int, when: str = "") -> list[str]:
    """Train from the checkpoint `latest` points to up to a step, as the run's job does: a
    checkpoint at every interval, and the registry written when the job exits."""
    entry = entry_of(run)
    when = when or stamp()
    start = latest(run)
    first = (step_of(start) if start else 0) + entry["checkpoint_every"]
    steps = list(range(first, to_step + 1, entry["checkpoint_every"]))
    written = []
    for index, step in enumerate(steps):
        at = earlier(when, 2 * (len(steps) - 1 - index))
        folder = write_checkpoint(run, step, entry["lr"], entry["launches"], at)
        beat(run, step, entry["lr"], at)
        written.append(folder.name)
    status = "finished" if to_step >= entry["total_steps"] else "paused"
    record(run, status=status, step=to_step, updated=when)
    return written


def jobs() -> dict:
    return read_json(QUEUE, {"next_id": 201, "jobs": []})


def submit(owner: str, kind: str, run: str, when: str, **more: object) -> dict:
    queue = jobs()
    job = {
        "id": f"J-{queue['next_id']:04d}",
        "owner": owner,
        "kind": kind,
        "run": run,
        "state": "pending",
        "submitted": when,
    } | more
    queue["next_id"] += 1
    queue["jobs"].append(job)
    write_json(QUEUE, queue)
    return job


def update_job(job_id: str, **fields: object) -> None:
    queue = jobs()
    for job in queue["jobs"]:
        if job["id"] == job_id:
            job.update(fields)
    write_json(QUEUE, queue)
EOF
cat > tools/runs <<'EOF'
#!/usr/bin/env python3
"""The lab's training runs, as the registry has them, and continuing a run."""

import argparse
import shutil
import sys

from _lab import (
    actor,
    entry_of,
    latest,
    point_latest,
    read_json,
    record,
    registry,
    run_dir,
    stamp,
    step_of,
    submit,
    train,
    write_json,
)


def listing() -> int:
    print(f"{'RUN':<34}{'STATUS':<10}{'STEP':>16}  UPDATED")
    for run, entry in sorted(registry().items()):
        steps = f"{entry['step']} of {entry['total_steps']}"
        print(f"{run:<34}{entry['status']:<10}{steps:>16}  {entry['updated']}")
    print(f"{len(registry())} runs in registry.json")
    return 0


def show(run: str) -> int:
    for key, value in entry_of(run).items():
        print(f"{key + ':':<18}{value}")
    notes = run_dir(run) / "NOTES.md"
    if notes.exists():
        print(f"\nruns/{run}/NOTES.md:")
        print("".join(f"  {line}\n" for line in notes.read_text().splitlines()), end="")
    return 0


def resume(run: str, to_step: int | None) -> int:
    entry = entry_of(run)
    start = latest(run)
    if start is None:
        sys.exit(f"runs: {run} has no checkpoint to resume from")
    settings = read_json(run_dir(run) / "config.json")
    target = to_step or settings["total_steps"]
    if target <= step_of(start):
        sys.exit(f"runs: {run} is already at step {step_of(start)}; give a later step with --to")
    settings["total_steps"] = max(settings["total_steps"], target)
    write_json(run_dir(run) / "config.json", settings)
    job = submit(run.split("/")[0], "resume", run, stamp(), to_step=target)
    (run_dir(run) / "RESUME").write_text(
        f"resume from {start.name} to step {target}\n"
        f"queued as {job['id']} by {actor()}, {stamp()}\n"
    )
    print(f"{run}: RESUME marker left, queued as {job['id']} (registry: {entry['status']})")
    return 0


def relaunch(run: str, into: str | None) -> int:
    entry = entry_of(run)
    if entry["status"] != "crashed":
        print(f"runs: {run} is {entry['status']} in the registry; only a crashed run is relaunched")
        return 1
    start = latest(run)
    if start is None:
        sys.exit(f"runs: {run} has no latest checkpoint")
    target = run
    if into:
        if run_dir(into).exists() or into in registry():
            sys.exit(f"runs: {into} already exists")
        run_dir(into).mkdir(parents=True)
        shutil.copytree(start, run_dir(into) / start.name)
        point_latest(into, start.name)
        shutil.copy2(run_dir(run) / "config.json", run_dir(into) / "config.json")
        (run_dir(into) / "NOTES.md").write_text(
            f"# {into}\n\nRelaunched {stamp()} by {actor()} from {run} at {start.name}.\n"
        )
        kept = {key: entry[key] for key in ("total_steps", "checkpoint_every", "lr", "config")}
        record(
            into,
            status="running",
            step=step_of(start),
            launches=1,
            relaunched_from=run,
            updated=stamp(),
            **kept,
        )
        record(run, continued_as=into)
        target = into
    else:
        record(run, status="running", launches=entry["launches"] + 1, updated=stamp())
    where = f", into {into}" if into else ""
    print(f"relaunch {run} from {start.name} (latest), lr {entry['lr']}{where}")
    for name in train(target, entry["total_steps"]):
        print(f"  wrote runs/{target}/{name}")
    print(f"{target}: finished at step {entry['total_steps']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/runs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every run in the registry")
    commands.add_parser("show", help="one run's registry entry and its notes").add_argument("run")
    resuming = commands.add_parser("resume", help="mark a run for resuming and queue it")
    resuming.add_argument("run")
    resuming.add_argument("--to", type=int, help="train to this step, past the configured last step")
    relaunching = commands.add_parser("relaunch", help="restart a crashed run from latest")
    relaunching.add_argument("run")
    relaunching.add_argument("--into", help="write the continuation to this new run instead")
    args = parser.parse_args()
    if args.command == "list":
        return listing()
    if args.command == "show":
        return show(args.run)
    if args.command == "resume":
        return resume(args.run, args.to)
    return relaunch(args.run, args.into)


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/ckpt <<'EOF'
#!/usr/bin/env python3
"""Checkpoints of a run: list, verify, retention and stripping optimizer states."""

import argparse
import shutil
import sys
from pathlib import Path

from _lab import checkpoints, digest, entry_of, kb, latest, read_json, registry, write_json

KEEP = 3
FILES = ("model.bin", "optimizer.bin")


def size_of(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


def state(folder: Path, name: str, manifest: dict) -> str:
    path = folder / name
    if not path.exists():
        return "none"
    return "ok" if digest(path) == manifest["files"].get(name) else "DAMAGED"


def listing(run: str) -> int:
    entry = entry_of(run)
    print(f"{run}  (registry: {entry['status']} at step {entry['step']} of {entry['total_steps']})")
    head = latest(run)
    total = 0
    for folder in checkpoints(run):
        manifest = read_json(folder / "MANIFEST.json")
        total += size_of(folder)
        files = "  ".join(f"{name} {state(folder, name, manifest)}" for name in FILES)
        mark = "  <- latest" if folder == head else ""
        print(f"  {folder.name:<12}{files:<36}lr {manifest['lr']:<8}{manifest['written']}{mark}")
    print(f"{len(checkpoints(run))} checkpoints, {kb(total)}")
    return 0


def verify(run: str) -> int:
    entry_of(run)
    damaged = 0
    for folder in checkpoints(run):
        manifest = read_json(folder / "MANIFEST.json")
        states = {name: state(folder, name, manifest) for name in FILES}
        damaged += "DAMAGED" in states.values() or states["model.bin"] == "none"
        found = "  ".join(f"{name} {value}" for name, value in states.items())
        print(f"  {folder.name:<12}{found}")
    print(f"{run}: {'DAMAGED' if damaged else 'intact'}")
    return 1 if damaged else 0


def retain(runs: list[str], keep: int, excepted: list[str], dry_run: bool) -> int:
    freed = 0
    for run in runs:
        entry_of(run)
        found = checkpoints(run)
        head = latest(run)
        old = found[: max(len(found) - keep, 0)]
        due = [f for f in old if f.name not in excepted and f != head]
        if not due:
            print(f"{run}: nothing to delete ({len(found)} checkpoints)")
            continue
        size = sum(size_of(f) for f in due)
        freed += size
        names = " ".join(f.name for f in due)
        kept = " ".join(f.name for f in found if f not in due)
        if dry_run:
            print(f"{run}: would delete {names} ({kb(size)}), would keep {kept}")
            continue
        for folder in due:
            shutil.rmtree(folder)
        print(f"{run}: deleted {names} ({kb(size)}), kept {kept}")
    print(f"retention: {kb(freed)} {'would be ' if dry_run else ''}freed, newest {keep} kept per run")
    return 0


def strip(runs: list[str], dry_run: bool) -> int:
    finished = sorted(run for run, entry in registry().items() if entry["status"] == "finished")
    freed = 0
    for run in runs or finished:
        entry = entry_of(run)
        if entry["status"] != "finished":
            print(f"{run}: skipped, the registry has it as {entry['status']}")
            continue
        states = [f / "optimizer.bin" for f in checkpoints(run) if (f / "optimizer.bin").exists()]
        if not states:
            print(f"{run}: no optimizer states left")
            continue
        size = sum(p.stat().st_size for p in states)
        freed += size
        if not dry_run:
            for path in states:
                manifest = read_json(path.parent / "MANIFEST.json")
                manifest["files"].pop("optimizer.bin", None)
                write_json(path.parent / "MANIFEST.json", manifest)
                path.unlink()
        print(
            f"{run}: finished at step {entry['step']}, "
            f"{'would remove' if dry_run else 'removed'} optimizer.bin from "
            f"{len(states)} checkpoint{'' if len(states) == 1 else 's'} ({kb(size)})"
        )
    print(f"strip: {kb(freed)} {'would be ' if dry_run else ''}freed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/ckpt", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="the checkpoints of a run").add_argument("run")
    commands.add_parser("verify", help="check a run's checkpoints against their manifests").add_argument("run")
    retaining = commands.add_parser("retain", help="keep the newest checkpoints, delete the rest")
    retaining.add_argument("runs", nargs="*")
    retaining.add_argument("--all", action="store_true", help="every run in the registry")
    retaining.add_argument("--keep", type=int, default=KEEP)
    retaining.add_argument("--except", dest="excepted", nargs="+", default=[], metavar="NAME")
    retaining.add_argument("--dry-run", action="store_true")
    stripping = commands.add_parser("strip", help="remove optimizer states from finished runs")
    stripping.add_argument("runs", nargs="*", help="default: every run the registry lists as finished")
    stripping.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "list":
        return listing(args.run)
    if args.command == "verify":
        return verify(args.run)
    if args.command == "retain":
        runs = sorted(registry()) if args.all else args.runs
        if not runs:
            sys.exit("ckpt: name the runs, or give --all")
        return retain(runs, args.keep, args.excepted, args.dry_run)
    return strip(args.runs, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/queue <<'EOF'
#!/usr/bin/env python3
"""The job queue of the CPU node. Pending jobs start in the nightly window."""

import argparse
import datetime
import sys

from _lab import QUEUE, TIME, actor, age, jobs, stamp, write_json

STALE_DAYS = 3
OPEN = ("pending", "held")


def what(job: dict) -> str:
    extra = f" {job['checkpoint']}" if job.get("checkpoint") else ""
    return f"{job['kind']} {job['run']}{extra}"


def since(job: dict) -> str:
    if job["state"] == "held":
        return f"on hold {age(job['held_since'])}"
    if job["state"] == "pending":
        return "next nightly window"
    return job.get(job["state"], "")


def is_stale(job: dict) -> bool:
    if job["state"] != "held":
        return False
    held = datetime.datetime.strptime(job["held_since"], TIME)
    return datetime.datetime.now() - held > datetime.timedelta(days=STALE_DAYS)


def table(rows: list[dict]) -> None:
    print(f"{'ID':<8}{'OWNER':<7}{'STATE':<11}{'JOB':<46}SINCE")
    for job in rows:
        print(f"{job['id']:<8}{job['owner']:<7}{job['state']:<11}{what(job):<46}{since(job)}")


def find(queue: dict, job_id: str) -> dict:
    for job in queue["jobs"]:
        if job["id"] == job_id:
            return job
    sys.exit(f"queue: no job {job_id}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/queue", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="pending and held jobs")
    listing.add_argument("--all", action="store_true", help="finished and cancelled jobs too")
    commands.add_parser("show", help="one job's record").add_argument("id")
    commands.add_parser("stale", help=f"jobs on hold for more than {STALE_DAYS} days")
    commands.add_parser("hold", help="put a pending job on hold").add_argument("id")
    commands.add_parser("release", help="release a held job").add_argument("id")
    cancelling = commands.add_parser("cancel", help="cancel jobs")
    cancelling.add_argument("ids", nargs="*")
    cancelling.add_argument("--stale", action="store_true", help="every stale job")
    args = parser.parse_args()
    queue = jobs()

    if args.command == "list":
        table([j for j in queue["jobs"] if args.all or j["state"] in OPEN])
        counts = {state: sum(j["state"] == state for j in queue["jobs"]) for state in OPEN}
        print(f"{counts['pending']} pending, {counts['held']} on hold")
    elif args.command == "show":
        for key, value in find(queue, args.id).items():
            print(f"{key + ':':<14}{value}")
    elif args.command == "stale":
        rows = [j for j in queue["jobs"] if is_stale(j)]
        table(rows)
        print(f"{len(rows)} job{'' if len(rows) == 1 else 's'} on hold for more than {STALE_DAYS} days")
    elif args.command in ("hold", "release"):
        job = find(queue, args.id)
        wanted = "pending" if args.command == "hold" else "held"
        if job["state"] != wanted:
            sys.exit(f"queue: {job['id']} is {job['state']}")
        if args.command == "hold":
            job.update(state="held", held_since=stamp())
        else:
            job.update(state="pending")
            del job["held_since"]
        write_json(QUEUE, queue)
        print(f"{job['id']}: {job['state']}")
    else:
        stale = [j for j in queue["jobs"] if is_stale(j)] if args.stale else []
        chosen = stale + [find(queue, job_id) for job_id in args.ids]
        if not chosen:
            print("queue: nothing to cancel")
            return 0
        for job in chosen:
            if job["state"] not in OPEN:
                print(f"{job['id']}: already {job['state']}")
                continue
            was = since(job)
            job.update(state="cancelled", cancelled=stamp(), cancelled_by=actor())
            print(f"cancelled {job['id']}  {job['owner']:<6}{what(job)}  ({was})")
        write_json(QUEUE, queue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/eval <<'EOF'
#!/usr/bin/env python3
"""Evaluate a checkpoint on the held-out split and keep the result in eval/results."""

import argparse
import hashlib
import sys

from _lab import RESULTS, digest, entry_of, latest, loss_at, read_json, run_dir, stamp, write_json


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every stored result")
    running = commands.add_parser("run", help="evaluate one checkpoint")
    running.add_argument("run")
    running.add_argument("--ckpt", help="checkpoint name; default: the one latest points to")
    args = parser.parse_args()

    if args.command == "list":
        print(f"{'RUN':<34}{'CHECKPOINT':<12}{'LOSS':>7}{'ACC':>8}  EVALUATED")
        results = [read_json(path) for path in sorted(RESULTS.glob("*.json"))]
        for r in results:
            print(
                f"{r['run']:<34}{r['checkpoint']:<12}{r['loss']:>7.3f}{r['accuracy']:>8.3f}"
                f"  {r['evaluated']}"
            )
        print(f"{len(results)} results in eval/results")
        return 0
    entry_of(args.run)
    folder = run_dir(args.run) / args.ckpt if args.ckpt else latest(args.run)
    if folder is None or not (folder / "model.bin").exists():
        sys.exit(f"eval: no model.bin for {args.run} {args.ckpt or 'latest'}")
    manifest = read_json(folder / "MANIFEST.json")
    model = digest(folder / "model.bin")
    noise = int(hashlib.sha256(model.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    loss = loss_at(manifest["step"], manifest["lr"]) + noise * 0.05
    result = {
        "run": args.run,
        "checkpoint": folder.name,
        "loss": round(loss, 3),
        "accuracy": round(max(0.0, 0.92 - loss / 6), 3),
        "model_sha256": model,
        "evaluated": stamp(),
    }
    path = RESULTS / f"{args.run.replace('/', '__')}__{folder.name}.json"
    write_json(path, result)
    print(f"{args.run} {folder.name}: loss {result['loss']:.3f}, accuracy {result['accuracy']:.3f}")
    print(f"wrote eval/results/{path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/data <<'EOF'
#!/usr/bin/env python3
"""The dataset shards in data/shards and the configs in configs/ that read them."""

import argparse
import sys

from _lab import CONFIGS, ROOT, SHARDS, kb, read_json


def readers() -> dict[str, list[str]]:
    """For every shard, the configs in configs/ that read it."""
    found: dict[str, list[str]] = {path.name: [] for path in sorted(SHARDS.glob("*.bin"))}
    for config in sorted(CONFIGS.glob("*.json")):
        for shard in read_json(config).get("data", []):
            found.setdefault(shard.split("/")[-1], []).append(config.name)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/data", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every shard")
    commands.add_parser("refs", help="which configs read each shard")
    pruning = commands.add_parser("prune", help="delete the shards no config reads")
    pruning.add_argument("--keep", nargs="+", default=[], metavar="NAME")
    pruning.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    found = readers()
    configs = len(list(CONFIGS.glob("*.json")))
    present = [name for name in found if (SHARDS / name).exists()]

    if args.command == "list":
        for name in present:
            print(f"{name:<28}{kb((SHARDS / name).stat().st_size):>9}")
        print(f"{len(present)} shards, {kb(sum((SHARDS / n).stat().st_size for n in present))}")
        return 0
    if args.command == "refs":
        print(f"{'SHARD':<28}READ BY")
        for name in present:
            print(f"{name:<28}{', '.join(found[name]) or '-'}")
        unread = sum(not found[name] for name in present)
        print(f"{configs} configs in configs/, {len(present)} shards, {unread} read by no config")
        return 0
    kept = {name if name.endswith(".bin") else f"{name}.bin" for name in args.keep}
    unknown = kept - set(present)
    if unknown:
        sys.exit(f"data: no shard named {', '.join(sorted(unknown))}")
    due = [name for name in present if not found[name] and name not in kept]
    print(f"scanned {configs} configs in configs/")
    if not due:
        print("prune: nothing to delete")
        return 0
    freed = 0
    for name in due:
        path = SHARDS / name
        freed += path.stat().st_size
        verb = "would delete" if args.dry_run else "deleted"
        print(f"{verb} {path.relative_to(ROOT)} ({kb(path.stat().st_size)})")
        if not args.dry_run:
            path.unlink()
    print(f"prune: {len(due)} shards, {kb(freed)} {'would be ' if args.dry_run else ''}freed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
chmod +x tools/runs tools/ckpt tools/queue tools/eval tools/data

python3 - <<'EOF'
import json
from pathlib import Path

corpus = [f"data/shards/corpus-a-{i:03d}.bin" for i in range(6)]
bpe = [f"data/shards/corpus-bpe-v1-{i:03d}.bin" for i in range(4)]
configs = {
    "char-lm-baseline": {"model": {"layers": 4, "width": 256}, "lr": 0.003, "total_steps": 16000, "data": corpus},
    "char-lm-bpe": {"model": {"layers": 4, "width": 256}, "lr": 0.003, "total_steps": 20000, "data": bpe},
    "char-lm-dropout": {"model": {"layers": 4, "width": 256, "dropout": 0.2}, "lr": 0.003, "total_steps": 12000, "data": corpus[:4]},
    "char-lm-wide": {"model": {"layers": 4, "width": 512}, "lr": 0.003, "total_steps": 24000, "data": corpus},
}
for name, settings in configs.items():
    whole = {"name": name, "checkpoint_every": 4000} | settings
    Path("configs", f"{name}.json").write_text(json.dumps(whole, indent=1) + "\n")
EOF
git add README.md .gitignore tools && as "${LENA[@]}" commit -q -m "Add the run, checkpoint, queue, eval and data tools"
git add configs/char-lm-baseline.json && as "${LENA[@]}" commit -q -m "Add the baseline config"
git add configs/char-lm-bpe.json configs/char-lm-dropout.json && as "${ANA[@]}" commit -q -m "Add the BPE and dropout configs"
git add configs/char-lm-wide.json && as "${KOFI[@]}" commit -q -m "Add the wide config"
git remote add origin /srv/git/origin.git
git push -q -u origin main

# The state of the lab: three finished runs, Kofi's wide run paused half way with its
# continuation queued, the shards the configs read, and each scientist's own folder.
python3 - <<'EOF'
import json
import sys
from pathlib import Path

sys.path.insert(0, "tools")
import _lab

_lab.SHARDS.mkdir(parents=True)
for name, count in (("corpus-a", 6), ("corpus-bpe-v1", 4)):
    for index in range(count):
        path = _lab.SHARDS / f"{name}-{index:03d}.bin"
        path.write_bytes(_lab.blob(path.name, _lab.SHARD_BYTES))

trained = [
    ("lena/char-lm-baseline", 16000, 240),
    ("ana/char-lm-bpe", 20000, 150),
    ("ana/char-lm-dropout", 12000, 100),
    ("kofi/char-lm-wide", 12000, 14),
]
for run, steps, hours in trained:
    scientist, name = run.split("/")
    _lab.create_run(run, f"configs/{name}.json", _lab.stamp(hours + 30))
    job = _lab.submit(scientist, "train", run, _lab.stamp(hours + 31))
    _lab.train(run, steps, _lab.stamp(hours))
    _lab.update_job(job["id"], state="done", done=_lab.stamp(hours))

for scientist, name, source in (("kofi", "wide-debug", "char-lm-wide"), ("ana", "dropout-short", "char-lm-dropout")):
    settings = json.loads(Path("configs", f"{source}.json").read_text())
    folder = Path("people", scientist, "configs")
    folder.mkdir(parents=True)
    short = settings | {"name": name, "total_steps": 2000, "checkpoint_every": 1000}
    (folder / f"{name}.json").write_text(json.dumps(short, indent=1) + "\n")
    (folder.parent / "README.md").write_text(
        f"{scientist.capitalize()}'s working folder: configs being tried before they go to configs/.\n"
    )
EOF
LAB_ACTOR=Kofi tools/runs resume kofi/char-lm-wide > /dev/null
for run in lena/char-lm-baseline ana/char-lm-bpe ana/char-lm-dropout; do
    tools/eval run "$run" > /dev/null
done
