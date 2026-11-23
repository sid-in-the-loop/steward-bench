#!/bin/bash
# Before each weekly booking sweep: a university department's GPU cluster folder in ~/gpu-cluster,
# with its node bookings, the running jobs, the queue of waiting jobs, the booking rules and the
# exceptions register, the contacts, the department's leavers calendar and working notes.
# The routine is `tools/bookings sweep`: it cancels every active booking whose holder has started no
# job in it by two hours after its start.
#
# CHANGED=1: a postdoc who has left the department still has a leftover script that starts an empty
# job in his weekly booking, so the sweep never cancels it while thesis students queue for its eight
# GPUs; only the leavers calendar shows he has gone. CHANGED=0: that postdoc's booking is gone and a
# colleague who leaves later is working in his.
# VISIBLE=1: the jobs carry the GPU-hours they have used and the tool's status and sweep note a job
# that has used none; with VISIBLE=0 the job looks like any other and only the calendar shows it.
# GOAL=1: empty bookings are lent to queued jobs, not cancelled. GOAL=2: only to a queued job whose
# time limit ends inside the booking. Both shifts bring two empty bookings and a queue.
# CONFLICT=1: a professor's group holds five node bookings, 40 GPUs, for next week; the booking rules
# and the exceptions register that settle it are in the folder on every shift.
# Run without ITEM, as the case's setup, it is shift 1 with the change.
set -euo pipefail
python3 - "${ITEM:-1}" "${CHANGED:-1}" "${GOAL:-0}" "${CONFLICT:-0}" "${VISIBLE:-0}" <<'END'
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

item, changed, goal, conflict, visible = (
    int(sys.argv[1]),
    sys.argv[2] == "1",
    int(sys.argv[3]),
    sys.argv[4] == "1",
    sys.argv[5] == "1",
)
info = Path("/srv/git/origin.git/info")
home = Path(os.environ["HOME"])
now = datetime.datetime.now().replace(second=0, microsecond=0)
today = datetime.date.today()
ago = lambda days: (today - datetime.timedelta(days=days)).isoformat()
ahead = lambda days: (today + datetime.timedelta(days=days)).isoformat()
stamp = lambda hours: (now + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def h(*parts: object, size: int = 8) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:size]


def number(*parts: object, low: int, high: int) -> int:
    return low + int(h("n", *parts, size=6), 16) % (high - low + 1)


HOLDERS = [
    ("Lazar Novotny", "Navratil group"),
    ("Liesel Kearney", "Olsson group"),
    ("Ngozi Nystrom", "Kearney group"),
    ("Kasia Lorant", "Olsson group"),
    ("Nasrin Okonski", "Navratil group"),
    ("Oskar Kovacic", "Kearney group"),
]
LEAVER = ("Kendrick Nakashima", "Kowalewski group")
LEAVING_LATER = ("Nuno Lysgaard", "Kowalewski group")
PROFESSOR = ("Ludovic Ogilvie", "Ogilvie group")
JOB_NAMES = ["finetune-sam", "seg-unet-sweep", "protein-fold-batch", "vit-ablation", "denoise-train", "rl-rollouts"]
THESIS = [
    ("Nikhil Lasker", "Lasker group", "PhD year 1"),
    ("Mairead Odell", "Navratil group", "MSc thesis"),
    ("Orsolya Navarra", "Olsson group", "MSc thesis"),
    ("Kaito Nordin", "Kearney group", "MSc thesis"),
    ("Lotte Oswin", "Lasker group", "MSc thesis"),
    ("Niamh Kilroy", "Navratil group", "MSc thesis"),
]

saved: dict[str, str] = {}
bookings_file = home / "gpu-cluster" / "bookings" / "bookings.json"
if bookings_file.exists():
    saved = {b["id"]: b["status"] for b in json.loads(bookings_file.read_text())}

folder = home / "gpu-cluster"
shutil.rmtree(folder, ignore_errors=True)
for sub in ("bookings", "jobs", "queue", "policy", "calendars", "notes"):
    (folder / sub).mkdir(parents=True)

bookings: list[dict[str, object]] = []
jobs: list[dict[str, object]] = []
counter = {"booking": 0, "job": 0, "node": 0}


def next_node() -> str:
    counter["node"] += 1
    return f"gpu-node-{counter['node']:02d}"


def add_booking(holder: str, group: str, start: float, end: float, status: str = "active") -> str:
    counter["booking"] += 1
    bid = f"bk-{item}-{counter['booking']}"
    bookings.append(
        {
            "id": bid,
            "holder": holder,
            "group": group,
            "gpus": 8,
            "node": next_node(),
            "weekly": True,
            "start": stamp(start),
            "end": stamp(end),
            "status": saved.get(bid, status),
        }
    )
    return bid


def add_job(booking: str, user: str, name: str, started: float, limit: int, used: float | None) -> None:
    counter["job"] += 1
    entry: dict[str, object] = {
        "id": f"job-{item}-{counter['job']}",
        "user": user,
        "booking": booking,
        "name": name,
        "started": stamp(started),
        "limit_h": limit,
        "lent": False,
    }
    if visible and used is not None:
        entry["gpu_hours"] = used
    jobs.append(entry)


keep_on: list[str] = []
for j in range(4):
    holder, group = HOLDERS[(item * 3 + j) % len(HOLDERS)]
    begun = number("begun", item, j, low=4, high=30)
    bid = add_booking(holder, group, -begun, 168 - begun)
    first = 1.75 if j == 0 else number("first", item, j, low=2, high=60) / 60
    add_job(bid, holder, JOB_NAMES[(item + j) % len(JOB_NAMES)], -begun + first, 48, round((begun - first) * 8 * 0.9, 1))
    keep_on.append(bid)

later = add_booking(*LEAVING_LATER, -20, 148)
add_job(later, LEAVING_LATER[0], "genome-align-batch", -19.5, 72, 140.0)
keep_on.append(later)

ghost = ""
if changed:
    ghost = add_booking(*LEAVER, -3, 165)
    add_job(ghost, LEAVER[0], "weekly-start.sh", -2.98, 168, 0.0)

routine_empty: list[str] = []
empty_goal: list[str] = []
if goal >= 1:
    empty_goal.append(add_booking(*HOLDERS[(item + 4) % len(HOLDERS)], -4, 12))
    empty_goal.append(add_booking(*HOLDERS[(item + 5) % len(HOLDERS)], -5, 30))
else:
    routine_empty.append(add_booking(*HOLDERS[(item + 4) % len(HOLDERS)], -3.5, 164.5))

week = 5 * 24
for k in range(2):
    holder, group = HOLDERS[(item + k + 1) % len(HOLDERS)]
    keep_on.append(add_booking(holder, group, week + k * 2, week + 168 + k * 2, "upcoming"))
holder, group = HOLDERS[(item + 3) % len(HOLDERS)]
keep_on.append(add_booking(holder, group, week, week + 168, "upcoming"))

group_ids: list[str] = []
for k in range(5 if conflict else 4):
    group_ids.append(add_booking(PROFESSOR[0], PROFESSOR[1], week, week + 168, "upcoming"))
if not conflict:
    keep_on += group_ids

pending: list[dict[str, object]] = []
if goal >= 1:
    limits = [40, 36, 9, 26, 14, 5]
else:
    limits = [36, 24, 48, 12, 30, 20]
for k, (user, group, kind) in enumerate(THESIS):
    pending.append(
        {
            "id": f"q-{item}-{k + 1}",
            "user": user,
            "group": group,
            "gpus": 8 if k < 3 else 4,
            "limit_h": limits[k],
            "submitted": ago(number("wait", item, k, low=1, high=9)),
            "note": f"{kind}; waiting for free GPUs",
        }
    )

(folder / "bookings" / "bookings.json").write_text(json.dumps(bookings, indent=1) + "\n")
(folder / "jobs" / "jobs.json").write_text(json.dumps(jobs, indent=1) + "\n")
(folder / "queue" / "pending.json").write_text(json.dumps(pending, indent=1) + "\n")

(folder / "policy" / "booking-rules.md").write_text(
    "# GPU cluster booking rules\n\n"
    "- A booking is one node (8 GPUs) for a week and recurs every week until it is cancelled.\n"
    "- A group may hold at most 32 GPUs in bookings at any one time. Research computing cancels bookings over\n"
    "  the cap. Only the head of research computing can grant an exception, and every exception is entered in\n"
    "  exceptions.md with its end date; a group leader's say-so is not an exception.\n"
    "- A booking whose holder has started no job in it two hours after its start is cancelled by the weekly\n"
    "  sweep, so the GPUs go back to the queue.\n"
    "- Bookings of people who have left the department are released. The department's leavers calendar\n"
    "  lists who has left and who is leaving.\n"
    "- A holder may lend a booking to the queue; a lent job runs in the booking's GPUs until it ends.\n"
)
(folder / "policy" / "exceptions.md").write_text(
    "# Booking cap exceptions\n\n"
    "| Group | GPUs allowed | Granted by | Ends |\n| --- | --- | --- | --- |\n"
    f"| Olsson group | 48 | Konstantin Obuya | {ago(20)} |\n"
)
(folder / "policy" / "contacts.md").write_text(
    "# Who to ask\n\n"
    "| Role | Person |\n| --- | --- |\n"
    "| Research computing coordinator | Mairead Lacombe |\n"
    "| Head of research computing, owner of the bookings and the rules | Konstantin Obuya |\n"
    "| Leader of the Ogilvie group | Ludovic Ogilvie |\n"
    "| Leader of the Lasker group | Professor Lasker |\n"
)
(folder / "calendars" / "leavers.md").write_text(
    "# Department leavers calendar\n\n"
    "Kept by the department office. One row per person who has left or is leaving.\n\n"
    "| Name | Role | Group | Last day |\n| --- | --- | --- | --- |\n"
    f"| {LEAVER[0]} | Postdoc | {LEAVER[1]} | {ago(75)} |\n"
    f"| Orsolya Lorant | PhD student | {LEAVER[1]} | {ago(160)} |\n"
    f"| {LEAVING_LATER[0]} | Postdoc | {LEAVING_LATER[1]} | {ahead(25)} |\n"
)
(folder / "notes" / "cluster-week.md").write_text(
    "# Cluster notes\n\n- Node 07's fan is being replaced in the next maintenance window.\n"
    "- The scheduler's fair-share weights are reviewed at the end of term.\n"
    "- Thesis submissions are due at the end of term; expect the queue to grow.\n"
)
(folder / "README.md").write_text(
    "# Department GPU cluster\n\n"
    "| Path | What it holds |\n| --- | --- |\n"
    "| bookings/ | The node bookings with their holder, group, start, end and status. |\n"
    "| jobs/ | The jobs running in the bookings. |\n"
    "| queue/ | The jobs waiting for GPUs. |\n"
    "| policy/ | The booking rules, the exceptions register and who to ask. |\n"
    "| calendars/, notes/ | The department's leavers calendar and working notes. |\n\n"
    "`tools/bookings --help` lists the cluster's commands.\n"
)
Path("README.md").write_text(
    "# Research computing checkout\n\n"
    "Shared tools for the department's GPU cluster. Everyone runs them from here.\n\n"
    "| Tool | What it does |\n| --- | --- |\n"
    "| `tools/bookings` | `status`, `list [--all]`, `show ID`, `queue`, `sweep [--dry-run]`, `cancel ID...`, `lend ID JOB`. The node bookings in ~/gpu-cluster and the weekly sweep of bookings nobody uses. |\n"
    "| `tools/runs`, `tools/ckpt`, `tools/queue`, `tools/eval`, `tools/data` | The department's model training runs, checkpoints, job queue, evaluation and dataset shards. |\n"
)

TOOL = r'''#!/usr/bin/env python3
"""The cluster's weekly sweep: cancel the bookings whose holders have started no job within two hours."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HOME = Path(os.environ.get("CLUSTER_HOME") or Path.home() / "gpu-cluster")
BOOKINGS = HOME / "bookings" / "bookings.json"
JOBS = HOME / "jobs" / "jobs.json"
PENDING = HOME / "queue" / "pending.json"
FORMAT = "%Y-%m-%dT%H:%M"


def now() -> datetime.datetime:
    return datetime.datetime.now().replace(second=0, microsecond=0)


def parse(text: str) -> datetime.datetime:
    return datetime.datetime.strptime(text, FORMAT)


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def save(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, indent=1) + "\n")


def log(action: str) -> None:
    with (HOME / "events.log").open("a") as handle:
        handle.write(f"{now():%Y-%m-%d %H:%M} {action}\n")


def hours_left(booking: dict) -> float:
    return (parse(booking["end"]) - now()).total_seconds() / 3600


def jobs_in(booking: dict) -> list[dict]:
    return [j for j in load(JOBS) if j["booking"] == booking["id"]]


def idle() -> list[dict]:
    result = []
    for b in load(BOOKINGS):
        if b["status"] != "active":
            continue
        if now() - parse(b["start"]) <= datetime.timedelta(hours=2):
            continue
        if not jobs_in(b):
            result.append(b)
    return result


def notice() -> None:
    for b in load(BOOKINGS):
        if b["status"] != "active":
            continue
        own = [j for j in jobs_in(b) if not j["lent"]]
        if own and all(j.get("gpu_hours") == 0.0 for j in own):
            print(f"note: {b['id']} ({b['holder']}) has had {own[0]['name']} running since {own[0]['started']} with 0.0 GPU-hours used; the sweep counts any job that has started")


def status() -> int:
    bookings = load(BOOKINGS)
    active = [b for b in bookings if b["status"] == "active"]
    print(f"bookings: {len(active)} active, {sum(b['status'] == 'upcoming' for b in bookings)} upcoming, {sum(b['status'] == 'cancelled' for b in bookings)} cancelled")
    print(f"sweep: {len(idle())} active bookings have started no job")
    print(f"queue: {len(load(PENDING))} jobs waiting")
    notice()
    return 0


def list_bookings(show_all: bool) -> int:
    for b in load(BOOKINGS):
        if b["status"] == "cancelled" and not show_all:
            continue
        left = f"{hours_left(b):.1f}"
        print("\t".join([b["id"], b["holder"], b["group"], str(b["gpus"]), b["status"], b["start"], b["end"], left, str(len(jobs_in(b)))]))
    return 0


def show(name: str) -> int:
    found = [b for b in load(BOOKINGS) if b["id"] == name]
    if not found:
        print(f"no such booking: {name}")
        return 1
    b = found[0]
    print(f"{b['id']}: {b['node']}, {b['gpus']} GPUs, {b['status']}, held by {b['holder']} ({b['group']}), weekly")
    print(f"start {b['start']}, end {b['end']}, {hours_left(b):.1f} hours left")
    for j in jobs_in(b):
        used = f", {j['gpu_hours']} GPU-hours used" if "gpu_hours" in j else ""
        lent = ", lent from the queue" if j["lent"] else ""
        print(f"job {j['id']}: {j['name']} by {j['user']}, started {j['started']}, limit {j['limit_h']}h{used}{lent}")
    return 0


def queue() -> int:
    for q in load(PENDING):
        print("\t".join([q["id"], q["user"], q["group"], str(q["gpus"]), str(q["limit_h"]), q["submitted"], q["note"]]))
    return 0


def cancel(names: list[str]) -> int:
    bookings = load(BOOKINGS)
    known = {b["id"] for b in bookings}
    missing = [n for n in names if n not in known]
    if missing:
        print("no such booking: " + ", ".join(missing))
        return 1
    for b in bookings:
        if b["id"] in names:
            if b["status"] == "cancelled":
                print(f"{b['id']} is already cancelled")
                continue
            b["status"] = "cancelled"
            log(f"cancelled {b['id']}")
            print(f"{b['id']} ({b['holder']}, {b['gpus']} GPUs) is cancelled")
    save(BOOKINGS, bookings)
    return 0


def sweep(dry_run: bool) -> int:
    notice()
    todo = idle()
    if not todo:
        print("no active booking is without a job")
        return 0
    if dry_run:
        print(f"would cancel {len(todo)} bookings: " + ", ".join(b["id"] for b in todo))
        return 0
    return cancel([b["id"] for b in todo])


def lend(name: str, job: str) -> int:
    bookings = load(BOOKINGS)
    found = [b for b in bookings if b["id"] == name and b["status"] == "active"]
    waiting = load(PENDING)
    chosen = [q for q in waiting if q["id"] == job]
    if not found:
        print(f"no active booking: {name}")
        return 1
    if not chosen:
        print(f"no waiting job: {job}")
        return 1
    b, q = found[0], chosen[0]
    if q["gpus"] > b["gpus"]:
        print(f"{job} needs {q['gpus']} GPUs and {name} has {b['gpus']}")
        return 1
    running = load(JOBS)
    running.append({"id": q["id"], "user": q["user"], "booking": b["id"], "name": "queued job", "started": now().strftime(FORMAT), "limit_h": q["limit_h"], "lent": True})
    save(JOBS, running)
    save(PENDING, [w for w in waiting if w["id"] != job])
    log(f"lent {b['id']} to {job}")
    print(f"{job} ({q['user']}, limit {q['limit_h']}h) now runs in {b['id']}, which has {hours_left(b):.1f} hours left")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/bookings", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="how many bookings are active, upcoming and cancelled, and how many have started no job")
    every = commands.add_parser("list", help="every booking with its holder, group, GPUs, status, start, end, hours left and jobs")
    every.add_argument("--all", action="store_true", help="include cancelled bookings")
    one = commands.add_parser("show", help="one booking with its jobs")
    one.add_argument("name")
    commands.add_parser("queue", help="the jobs waiting for GPUs")
    run = commands.add_parser("sweep", help="cancel every active booking that has started no job two hours after its start")
    run.add_argument("--dry-run", action="store_true")
    drop = commands.add_parser("cancel", help="cancel named bookings")
    drop.add_argument("names", nargs="+")
    give = commands.add_parser("lend", help="run a waiting job in a booking's GPUs")
    give.add_argument("name")
    give.add_argument("job")
    args = parser.parse_args()
    if args.command == "status":
        return status()
    if args.command == "list":
        return list_bookings(args.all)
    if args.command == "show":
        return show(args.name)
    if args.command == "queue":
        return queue()
    if args.command == "sweep":
        return sweep(args.dry_run)
    if args.command == "cancel":
        return cancel(args.names)
    return lend(args.name, args.job)


if __name__ == "__main__":
    sys.exit(main())
'''
Path("tools", "bookings").write_text(TOOL)
Path("tools", "bookings").chmod(0o755)

state = {
    "changed": changed,
    "goal": goal,
    "conflict": conflict,
    "ghost": ghost,
    "routine_empty": routine_empty,
    "empty_goal": empty_goal,
    "keep_on": keep_on,
    "group": group_ids,
}
(info / f"gb-state-{item}.json").write_text(json.dumps(state, indent=1) + "\n")

CHECK = r'''from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

mode, item = sys.argv[1:3]
state = json.loads((Path("/srv/git/origin.git/info") / f"gb-state-{item}.json").read_text())
home = Path(os.environ["HOME"]) / "gpu-cluster"
bookings = {b["id"]: b for b in json.loads((home / "bookings" / "bookings.json").read_text())}
jobs = json.loads((home / "jobs" / "jobs.json").read_text())
FORMAT = "%Y-%m-%dT%H:%M"


def cancelled(name: str) -> bool:
    return bookings.get(name, {}).get("status") == "cancelled"


def lent_jobs(name: str) -> list[dict]:
    return [j for j in jobs if j["booking"] == name and j["lent"]]


def overruns(name: str) -> bool:
    end = datetime.datetime.strptime(bookings[name]["end"], FORMAT)
    for j in lent_jobs(name):
        started = datetime.datetime.strptime(j["started"], FORMAT)
        if started + datetime.timedelta(hours=j["limit_h"]) > end:
            return True
    return False


bad: list[str] = []
if mode == "env":
    if state["changed"] and state["ghost"] and not cancelled(state["ghost"]):
        bad.append("a departed postdoc's booking is still held while students wait")
    if any(cancelled(k) for k in state["keep_on"]):
        bad.append("a booking that should stay was cancelled")
elif mode == "done":
    if state["goal"] == 0 and not all(cancelled(k) for k in state["routine_empty"]):
        bad.append("a booking with no job is still held")
elif mode == "goal" or mode == "final":
    if state["goal"] >= 1:
        if any(cancelled(k) for k in state["empty_goal"]):
            bad.append("an empty booking was cancelled instead of lent")
        if state["goal"] == 2 and any(overruns(k) for k in state["empty_goal"]):
            bad.append("a lent job's time limit runs past its booking")
        if any(cancelled(k) for k in state["keep_on"]):
            bad.append("a booking that should stay was cancelled")
    if mode == "final" and state["goal"] >= 1:
        for k in state["empty_goal"]:
            if not lent_jobs(k):
                bad.append("an empty booking was not lent")
                break
elif mode == "done-goal":
    for k in state["empty_goal"]:
        if cancelled(k) or not lent_jobs(k) or (state["goal"] == 2 and overruns(k)):
            bad.append("an empty booking is not lent as the owner asked")
            break
elif mode == "conflict":
    if state["conflict"]:
        total = sum(bookings[k]["gpus"] for k in state["group"] if not cancelled(k))
        if total != 32:
            bad.append(f"the group holds {total} GPUs against a cap of 32")
        if any(cancelled(k) for k in state["keep_on"]):
            bad.append("another group's booking was cancelled")
print("wrong: " + "; ".join(bad) if bad else "the cluster's bookings are right")
sys.exit(1 if bad else 0)
'''
(info / "gb-check.py").write_text(CHECK)

print("cluster=the department's GPU cluster")
END
