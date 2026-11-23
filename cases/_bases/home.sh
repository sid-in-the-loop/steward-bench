#!/bin/bash
# A neutral assistant workspace for the Miscellaneous domain: small command-line tools over plain
# files for devices, bookings, payments, messages and calendars, plus the scheduled-jobs runner.
# It holds no people, places or history; each task's item.sh stages its own world on top. State
# is kept as files under var/ (not committed) and the calendar under ~/data/calendar. Run in the
# empty working directory.
set -euo pipefail

git config --global user.name "assistant"
git config --global user.email "assistant@home.example"
git config --global init.defaultBranch main
git config --global advice.detachedHead false
git init -q .

mkdir -p tools jobs etc
cat > .gitignore <<'EOF'
var/
__pycache__/
EOF
cat > README.md <<'EOF'
# home

Command-line tools for an assistant agent. Each tool keeps its state as plain files, so
anything it changes can be read back with `cat`.

| Tool | What it does |
| --- | --- |
| `tools/devices` | `list`, `show NAME`, `set NAME KEY VALUE`, `run NAME ACTION`. One state file per device in `var/devices/`; the file's `actions:` line says what each action leaves the device in. |
| `tools/bookings` | `list [--status S]`, `show ID`, `create --kind K --what W --who P --start T --end T`, `cancel ID`, `extend ID --to T`. Bookings are JSON files in `var/bookings/`. Two active bookings of the same thing may not overlap. |
| `tools/pay` | `list [--status S]`, `pay PAYEE AMOUNT [--memo M]`, `refund ID`, `schedule PAYEE AMOUNT --on DATE [--memo M]`, `cancel ID`, `run-due [--at T]`. The ledger is `var/pay/ledger.csv`. |
| `tools/messages` | `threads`, `read THREAD [--last N]`, `send THREAD TEXT`. A thread is a JSON-lines file in `var/messages/`; every sent message is also recorded in `var/log/sent.log`. |
| `tools/calendar` | `list [--on DATE] [--who NAME]`, `show ID`. Events are `key: value` files in `~/data/calendar/`. |
| `tools/jobs` | `list`, `show NAME`, `enable NAME`, `disable NAME`, `run NAME`, `run-due [--at T]`. Jobs are the files in `jobs/`. Cron calls `run-due` every five minutes (see `etc/crontab`); a job's command runs from this directory with `DATA` and `VAR` set. |

Times are written `YYYY-MM-DD HH:MM`.

## Layout

- `jobs/`: scheduled jobs, one `NAME.job` file each, with `schedule: daily HH:MM` and `command:`.
- `var/`: state written by the tools. Not committed. `HOME_VAR` moves it.
- `~/data/`: files the tools read, such as the calendar. `HOME_DATA` moves it.
- `HOME_ACTOR` names who is acting; `HOME_NOW` fixes the clock the tools use.
EOF
cat > tools/_home.py <<'EOF'
"""Paths and helpers shared by the tools."""

import datetime
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("HOME_VAR", ROOT / "var"))
DATA = Path(os.environ.get("HOME_DATA", Path.home() / "data"))
TIME = "%Y-%m-%d %H:%M"
DAY = "%Y-%m-%d"


def actor() -> str:
    """Who is running the tool: HOME_ACTOR if set, otherwise the git user."""
    name = os.environ.get("HOME_ACTOR")
    if not name:
        git = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, cwd=ROOT
        )
        name = git.stdout.strip()
    return name or "unknown"


def current() -> datetime.datetime:
    """The clock: HOME_NOW if set, so a night can be simulated."""
    fixed = os.environ.get("HOME_NOW")
    return datetime.datetime.strptime(fixed, TIME) if fixed else datetime.datetime.now()


def now() -> str:
    return current().strftime(TIME)


def parse_time(text: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(text, TIME)
    except ValueError:
        raise SystemExit(f"error: {text!r} is not a time written YYYY-MM-DD HH:MM") from None


def read_fields(path: Path) -> dict[str, str]:
    """A file of `key: value` lines; lines starting with # are comments."""
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def write_fields(path: Path, fields: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}: {value}\n" for key, value in fields.items()))


def log(name: str, line: str) -> None:
    (VAR / "log").mkdir(parents=True, exist_ok=True)
    with (VAR / "log" / name).open("a") as handle:
        handle.write(f"{now()} {actor()} {line}\n")
EOF
cat > tools/devices <<'EOF'
#!/usr/bin/env python3
"""Devices: locks, robots, drones, vehicles, thermostats, feeders, batteries, cameras."""

import argparse
import sys
from pathlib import Path

from _home import VAR, log, read_fields, write_fields

DEVICES = VAR / "devices"


def path_of(name: str) -> Path:
    path = DEVICES / f"{name}.state"
    if not path.exists():
        sys.exit(f"devices: no such device {name}")
    return path


def actions_of(fields: dict[str, str]) -> dict[str, str]:
    """`actions: lock=locked, unlock=unlocked`: each action and the state it leaves."""
    result: dict[str, str] = {}
    for part in fields.get("actions", "").split(","):
        if part.strip():
            action, _, state = part.partition("=")
            result[action.strip()] = state.strip() or action.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/devices", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every device with its kind and state")
    commands.add_parser("show", help="all of a device's state").add_argument("name")
    setting = commands.add_parser("set", help="change one setting")
    setting.add_argument("name")
    setting.add_argument("key")
    setting.add_argument("value")
    running = commands.add_parser("run", help="run an action the device offers")
    running.add_argument("name")
    running.add_argument("action")
    args = parser.parse_args()

    if args.command == "list":
        print(f"{'NAME':<20}{'KIND':<14}STATE")
        for path in sorted(DEVICES.glob("*.state")):
            fields = read_fields(path)
            print(f"{path.stem:<20}{fields.get('kind', '-'):<14}{fields.get('state', '-')}")
        return 0
    path = path_of(args.name)
    fields = read_fields(path)
    if args.command == "show":
        for key, value in fields.items():
            print(f"{key + ':':<16}{value}")
    elif args.command == "set":
        allowed = [s.strip() for s in fields.get("settings", "").split(",") if s.strip()]
        if allowed and args.key not in allowed:
            print(f"devices: {args.name} has no setting {args.key} (settings: {', '.join(allowed)})")
            return 1
        fields[f"setting.{args.key}"] = args.value
        write_fields(path, fields)
        log("devices.log", f"{args.name} set {args.key}={args.value}")
        print(f"{args.name}: {args.key} set to {args.value}")
    else:
        offered = actions_of(fields)
        if args.action not in offered:
            print(f"devices: {args.name} cannot {args.action} (actions: {', '.join(offered)})")
            return 1
        fields["state"] = offered[args.action]
        fields["last_action"] = args.action
        write_fields(path, fields)
        log("devices.log", f"{args.name} {args.action}")
        print(f"{args.name}: {args.action} done, state is {fields['state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/bookings <<'EOF'
#!/usr/bin/env python3
"""Bookings: rentals, appointments, slots and reservations, one JSON file each."""

import argparse
import json
import sys
from pathlib import Path

from _home import VAR, actor, log, now, parse_time

BOOKINGS = VAR / "bookings"


def load(booking_id: str) -> dict[str, str]:
    path = BOOKINGS / f"{booking_id}.json"
    if not path.exists():
        sys.exit(f"bookings: no such booking {booking_id}")
    return json.loads(path.read_text())


def save(booking: dict[str, str]) -> None:
    BOOKINGS.mkdir(parents=True, exist_ok=True)
    path: Path = BOOKINGS / f"{booking['id']}.json"
    path.write_text(json.dumps(booking, indent=1) + "\n")


def everything() -> list[dict[str, str]]:
    return [json.loads(p.read_text()) for p in sorted(BOOKINGS.glob("*.json"))]


def clash(what: str, start: str, end: str, ignoring: str = "") -> dict[str, str] | None:
    """An active booking of the same thing that overlaps the interval."""
    for other in everything():
        if other["id"] == ignoring or other["what"] != what or other["status"] != "active":
            continue
        if parse_time(start) < parse_time(other["end"]) and parse_time(other["start"]) < parse_time(end):
            return other
    return None


def line(b: dict[str, str]) -> str:
    return f"{b['id']}  {b['status']:<10}{b['kind']:<12}{b['what']:<20}{b['start']} to {b['end']}  {b['who']}"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/bookings", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="bookings, newest last")
    listing.add_argument("--status", help="active or cancelled")
    commands.add_parser("show", help="one booking and its history").add_argument("id")
    creating = commands.add_parser("create", help="make a booking")
    for option in ("kind", "what", "who", "start", "end"):
        creating.add_argument(f"--{option}", required=True)
    commands.add_parser("cancel", help="cancel a booking").add_argument("id")
    extending = commands.add_parser("extend", help="move a booking's end")
    extending.add_argument("id")
    extending.add_argument("--to", required=True, help="the new end time")
    args = parser.parse_args()

    if args.command == "list":
        shown = [b for b in everything() if not args.status or b["status"] == args.status]
        for booking in shown:
            print(line(booking))
        print(f"{len(shown)} bookings")
    elif args.command == "show":
        booking = load(args.id)
        for key, value in booking.items():
            if key != "history":
                print(f"{key + ':':<10}{value}")
        for entry in booking["history"]:
            print(f"  {entry}")
    elif args.command == "create":
        if parse_time(args.end) <= parse_time(args.start):
            print("bookings: the end must be after the start")
            return 1
        other = clash(args.what, args.start, args.end)
        if other:
            print(f"bookings: {args.what} is already booked {other['start']} to {other['end']} ({other['id']})")
            return 1
        number = max([int(b["id"].split("-")[1]) for b in everything()], default=0) + 1
        booking = {
            "id": f"BK-{number:04d}", "status": "active", "kind": args.kind, "what": args.what,
            "who": args.who, "start": args.start, "end": args.end,
            "history": [f"{now()} {actor()} created"],
        }
        save(booking)
        log("bookings.log", f"{booking['id']} created")
        print(f"{booking['id']} created: {args.what} {args.start} to {args.end}")
    elif args.command == "cancel":
        booking = load(args.id)
        if booking["status"] != "active":
            print(f"{args.id} is already {booking['status']}")
            return 1
        booking["status"] = "cancelled"
        booking["history"].append(f"{now()} {actor()} cancelled")
        save(booking)
        log("bookings.log", f"{args.id} cancelled")
        print(f"{args.id} cancelled")
    else:
        booking = load(args.id)
        if booking["status"] != "active":
            print(f"{args.id} is {booking['status']} and cannot be extended")
            return 1
        if parse_time(args.to) <= parse_time(booking["start"]):
            print("bookings: the new end must be after the start")
            return 1
        other = clash(booking["what"], booking["start"], args.to, ignoring=args.id)
        if other:
            print(f"bookings: cannot extend, {booking['what']} is booked {other['start']} to {other['end']} ({other['id']})")
            return 1
        booking["history"].append(f"{now()} {actor()} extended from {booking['end']} to {args.to}")
        booking["end"] = args.to
        save(booking)
        log("bookings.log", f"{args.id} extended to {args.to}")
        print(f"{args.id} extended to {args.to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/pay <<'EOF'
#!/usr/bin/env python3
"""Payments, kept in the ledger var/pay/ledger.csv. Amounts are decimals with two places."""

import argparse
import csv
import datetime
import sys
from decimal import Decimal, InvalidOperation

from _home import DAY, VAR, actor, current, log, now

LEDGER = VAR / "pay" / "ledger.csv"
FIELDS = ["id", "date", "payee", "amount", "status", "memo"]


def rows() -> list[dict[str, str]]:
    if not LEDGER.exists():
        return []
    with LEDGER.open(newline="") as handle:
        return list(csv.DictReader(handle))


def save(entries: list[dict[str, str]]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(entries)


def amount_of(text: str) -> Decimal:
    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise SystemExit(f"pay: {text!r} is not an amount") from None
    if value <= 0:
        raise SystemExit("pay: the amount must be positive")
    return value


def new_id(entries: list[dict[str, str]]) -> str:
    return f"P-{max([int(e['id'].split('-')[1]) for e in entries], default=0) + 1:04d}"


def find(entries: list[dict[str, str]], entry_id: str) -> dict[str, str]:
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    raise SystemExit(f"pay: no such entry {entry_id}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/pay", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="the ledger")
    listing.add_argument("--status", help="paid, scheduled, refunded or cancelled")
    paying = commands.add_parser("pay", help="pay now")
    paying.add_argument("payee")
    paying.add_argument("amount")
    paying.add_argument("--memo", default="")
    refunding = commands.add_parser("refund", help="get a paid entry back")
    refunding.add_argument("id")
    scheduling = commands.add_parser("schedule", help="pay on a date")
    scheduling.add_argument("payee")
    scheduling.add_argument("amount")
    scheduling.add_argument("--on", required=True, help="YYYY-MM-DD")
    scheduling.add_argument("--memo", default="")
    commands.add_parser("cancel", help="cancel a scheduled payment").add_argument("id")
    due = commands.add_parser("run-due", help="pay every scheduled entry whose date has come")
    due.add_argument("--at", help="act as if it were this time (YYYY-MM-DD HH:MM)")
    args = parser.parse_args()
    entries = rows()

    if args.command == "list":
        print(f"{'ID':<8}{'DATE':<12}{'STATUS':<11}{'AMOUNT':>10}  {'PAYEE':<20}MEMO")
        for e in entries:
            if not args.status or e["status"] == args.status:
                print(f"{e['id']:<8}{e['date']:<12}{e['status']:<11}{e['amount']:>10}  {e['payee']:<20}{e['memo']}")
        paid = sum((Decimal(e["amount"]) for e in entries if e["status"] == "paid"), Decimal(0))
        print(f"paid in total: {paid}")
    elif args.command in ("pay", "schedule"):
        amount = amount_of(args.amount)
        if args.command == "schedule":
            try:
                datetime.datetime.strptime(args.on, DAY)
            except ValueError:
                print(f"pay: {args.on!r} is not a date written YYYY-MM-DD")
                return 1
        status, date = ("paid", now()[:10]) if args.command == "pay" else ("scheduled", args.on)
        entry = {"id": new_id(entries), "date": date, "payee": args.payee, "amount": str(amount),
                 "status": status, "memo": args.memo}
        save(entries + [entry])
        log("pay.log", f"{entry['id']} {status} {amount} {args.payee}")
        print(f"{entry['id']} {status}: {amount} to {args.payee} ({date})")
    elif args.command == "refund":
        entry = find(entries, args.id)
        if entry["status"] != "paid":
            print(f"pay: {args.id} is {entry['status']}, only a paid entry can be refunded")
            return 1
        entry["status"] = "refunded"
        save(entries)
        log("pay.log", f"{args.id} refunded")
        print(f"{args.id} refunded: {entry['amount']} from {entry['payee']}")
    elif args.command == "cancel":
        entry = find(entries, args.id)
        if entry["status"] != "scheduled":
            print(f"pay: {args.id} is {entry['status']}, only a scheduled entry can be cancelled")
            return 1
        entry["status"] = "cancelled"
        save(entries)
        log("pay.log", f"{args.id} cancelled")
        print(f"{args.id} cancelled")
    else:
        at = datetime.datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else current()
        settled = 0
        for entry in entries:
            if entry["status"] == "scheduled" and entry["date"] <= at.strftime(DAY):
                entry["status"] = "paid"
                log("pay.log", f"{entry['id']} paid {entry['amount']} {entry['payee']} (scheduled for {entry['date']})")
                print(f"{entry['id']} paid: {entry['amount']} to {entry['payee']}")
                settled += 1
        save(entries)
        print(f"run-due: {settled} payment{'s' if settled != 1 else ''} made by {actor()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/messages <<'EOF'
#!/usr/bin/env python3
"""Messages in named threads, such as a family chat or a customer thread."""

import argparse
import json
import sys

from _home import VAR, actor, log, now

THREADS = VAR / "messages"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/messages", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("threads", help="every thread and how many messages it holds")
    reading = commands.add_parser("read", help="a thread, oldest first")
    reading.add_argument("thread")
    reading.add_argument("--last", type=int, help="only the last N messages")
    sending = commands.add_parser("send", help="send a message to a thread")
    sending.add_argument("thread")
    sending.add_argument("text")
    args = parser.parse_args()

    if args.command == "threads":
        for path in sorted(THREADS.glob("*.jsonl")):
            print(f"{path.stem:<24}{len(path.read_text().splitlines())} messages")
        return 0
    path = THREADS / f"{args.thread}.jsonl"
    if args.command == "read":
        if not path.exists():
            sys.exit(f"messages: no such thread {args.thread}")
        found = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for message in found[-args.last :] if args.last else found:
            print(f"{message['at']}  {message['from']}: {message['text']}")
    else:
        if not path.exists():
            sys.exit(f"messages: no such thread {args.thread}")
        message = {"at": now(), "from": actor(), "text": args.text}
        with path.open("a") as handle:
            handle.write(json.dumps(message) + "\n")
        log("sent.log", f"{args.thread}: {args.text}")
        print(f"sent to {args.thread}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/calendar <<'EOF'
#!/usr/bin/env python3
"""Calendar events, read from `key: value` files in ~/data/calendar."""

import argparse
import sys

from _home import DATA, read_fields

EVENTS = DATA / "calendar"


def events() -> list[tuple[str, dict[str, str]]]:
    found = [(p.stem, read_fields(p)) for p in sorted(EVENTS.glob("*.event"))]
    return sorted(found, key=lambda item: item[1].get("start", ""))


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/calendar", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="events by start time")
    listing.add_argument("--on", help="only events that start on this date (YYYY-MM-DD)")
    listing.add_argument("--who", help="only events whose `who` field names this person")
    commands.add_parser("show", help="one event").add_argument("id")
    args = parser.parse_args()

    if args.command == "list":
        count = 0
        for event_id, fields in events():
            if args.on and not fields.get("start", "").startswith(args.on):
                continue
            if args.who and args.who.lower() not in fields.get("who", "").lower():
                continue
            count += 1
            print(f"{event_id:<20}{fields.get('start', '?')} to {fields.get('end', '?')}  {fields.get('title', '')}")
        print(f"{count} events")
    else:
        path = EVENTS / f"{args.id}.event"
        if not path.exists():
            sys.exit(f"calendar: no such event {args.id}")
        for key, value in read_fields(path).items():
            print(f"{key + ':':<10}{value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
cat > tools/jobs <<'EOF'
#!/usr/bin/env python3
"""Scheduled jobs. Each file in jobs/ is one job; cron calls `run-due` every five minutes."""

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

from _home import DATA, ROOT, TIME, VAR, actor, current, now, read_fields

STATE = VAR / "jobs"


def job(name: str) -> dict[str, str]:
    path = ROOT / "jobs" / f"{name}.job"
    if not path.exists():
        sys.exit(f"jobs: no such job {name}")
    fields = read_fields(path)
    kind, _, clock = fields.get("schedule", "").partition(" ")
    if kind != "daily" or "command" not in fields:
        sys.exit(f"jobs: {path.name} needs `schedule: daily HH:MM` and `command:`")
    fields["name"], fields["clock"] = name, clock
    return fields


def names() -> list[str]:
    return sorted(path.stem for path in (ROOT / "jobs").glob("*.job"))


def stamp(name: str, suffix: str) -> datetime.datetime | None:
    path = STATE / f"{name}.{suffix}"
    if not path.exists():
        return None
    return datetime.datetime.strptime(path.read_text().splitlines()[0], TIME)


def scheduled(clock: str, at: datetime.datetime) -> datetime.datetime:
    """The latest scheduled time that is not after `at`."""
    hour, minute = (int(part) for part in clock.split(":"))
    time = at.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return time if time <= at else time - datetime.timedelta(days=1)


def run(entry: dict[str, str], at: datetime.datetime) -> int:
    environment = dict(os.environ, DATA=str(DATA), VAR=str(VAR), HOME_NOW=at.strftime(TIME))
    done = subprocess.run(
        ["bash", "-c", entry["command"]], cwd=ROOT, env=environment, capture_output=True, text=True
    )
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{entry['name']}.last").write_text(at.strftime(TIME) + "\n")
    print(f"{entry['name']}: {entry['command']}")
    for line in (done.stdout + done.stderr).splitlines():
        print(f"  {line}")
    result = "ok" if done.returncode == 0 else f"failed (exit {done.returncode})"
    print(f"{entry['name']}: {result}")
    (VAR / "log").mkdir(parents=True, exist_ok=True)
    with (VAR / "log" / "jobs.log").open("a") as log:
        log.write(f"{at.strftime(TIME)} {entry['name']} {result}\n")
    return done.returncode


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/jobs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("show", "enable", "disable", "run"):
        commands.add_parser(command).add_argument("name")
    due = commands.add_parser("run-due", help="run every enabled job whose time has come")
    due.add_argument("--at", help="act as if it were this time (YYYY-MM-DD HH:MM), to catch up")
    args = parser.parse_args()
    today = current()

    if args.command == "list":
        print(f"{'NAME':<18}{'STATE':<10}{'SCHEDULE':<14}{'LAST RUN':<19}NEXT RUN")
        for name in names():
            entry = job(name)
            enabled = (STATE / f"{name}.enabled").exists()
            last = stamp(name, "last")
            following = scheduled(entry["clock"], today) + datetime.timedelta(days=1)
            print(
                f"{name:<18}{'enabled' if enabled else 'disabled':<10}{entry['schedule']:<14}"
                f"{last.strftime(TIME) if last else 'never':<19}"
                f"{following.strftime(TIME) if enabled else '-'}"
            )
    elif args.command == "show":
        entry = job(args.name)
        enabled = STATE / f"{args.name}.enabled"
        print(Path(ROOT / "jobs" / f"{args.name}.job").read_text().rstrip())
        if enabled.exists():
            print(f"state: enabled ({enabled.read_text().splitlines()[1]})")
        else:
            print("state: disabled")
        last = stamp(args.name, "last")
        print(f"last run: {last.strftime(TIME) if last else 'never'}")
    elif args.command == "enable":
        job(args.name)
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / f"{args.name}.enabled").write_text(f"{now()}\nby {actor()}\n")
        print(f"{args.name}: enabled")
    elif args.command == "disable":
        job(args.name)
        (STATE / f"{args.name}.enabled").unlink(missing_ok=True)
        print(f"{args.name}: disabled")
    elif args.command == "run":
        return run(job(args.name), today)
    else:
        at = datetime.datetime.strptime(args.at, TIME) if args.at else today
        ran = 0
        for name in names():
            entry = job(name)
            since = max(filter(None, [stamp(name, "last"), stamp(name, "enabled")]), default=None)
            if since is not None and stamp(name, "enabled") and since < scheduled(entry["clock"], at):
                run(entry, at)
                ran += 1
        print(f"run-due: {ran} job{'s' if ran != 1 else ''} run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
chmod +x tools/devices tools/bookings tools/pay tools/messages tools/calendar tools/jobs

cat > etc/crontab <<'EOF'
*/5 * * * * assistant cd "$HOME/home" && tools/jobs run-due >> var/log/cron.log 2>&1
EOF
mkdir -p ~/data/calendar
git add -A && git commit -q -m "Add the assistant tools and the scheduled jobs runner"
