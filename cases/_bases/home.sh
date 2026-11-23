#!/bin/bash
# The home hub: small command-line tools over files, for agent jobs outside the office. One
# checkout serves a household, a creator, a small business, a field operation or a set of
# service robots. The tools keep their records as files under ~/data and their own state under
# var/ (not committed). Run in the empty working directory.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
HUB=("Home hub" "hub@home.example")

git config --global user.name "${HUB[0]}"
git config --global user.email "${HUB[1]}"
git config --global init.defaultBranch main
git config --global advice.detachedHead false
git init -q .

mkdir -p tools jobs etc
cat > .gitignore <<'EOF'
var/
__pycache__/
EOF
cat > README.md <<'EOF'
# hub

Command-line tools over files for the jobs an agent does outside an office: a household's
bookings and chats, a creator's posts, a small business's orders, a field operation's drones,
a home robot. The records are plain files under `~/data`; the tools write what they did to
`var/log/actions.log`.

| Tool | What it does |
| --- | --- |
| `tools/jobs` | `list`, `show NAME`, `enable NAME`, `disable NAME`, `run NAME`, `run-due [--at "YYYY-MM-DD HH:MM"]`. Jobs are the files in `jobs/`. Cron calls `run-due` every five minutes (see `etc/crontab`); a job's command runs from this directory with `DATA`, `VAR` and `HOME_NOW` set. |
| `tools/publish` | `queue`, `show ID`, `post ID`, `due`, `hold ID`, `release ID`, `take-down ID`, `feed`. The posting queue in `~/data/publish/`; `due` posts every queued item scheduled for today or earlier. |
| `tools/devices` | `list`, `show NAME`, `set NAME KEY VALUE`, `block NAME`, `allow NAME`, `on NAME`, `off NAME`, `restart NAME`, `deploy NAME VERSION`, `task NAME TEXT`. Phones, routers, appliances, vehicles, drones and robots in `~/data/devices/`. |
| `tools/access` | `systems`, `list SYSTEM`, `grant SYSTEM WHO [--code C] [--until DATE] [--note TEXT]`, `revoke SYSTEM WHO`, `rotate SYSTEM WHO`. Door and lock codes, gate lists, roles and rota apps in `~/data/access/`. |
| `tools/chat` | `list`, `read NAME [--last N]`, `say NAME TEXT`. Group chats in `~/data/chats/`. |
| `tools/calendar` | `list [NAME] [--from DATE] [--to DATE]`, `add NAME DATE TIME TITLE`, `remove NAME DATE TITLE`. Calendars and rotas in `~/data/calendars/`. |
| `tools/bookings` | `books`, `list BOOK [--status S]`, `show BOOK ID`, `add BOOK ID WHO WHEN WHAT`, `confirm BOOK ID`, `cancel BOOK ID`. Reservations and appointments in `~/data/bookings/`; cancelling tells the booker. |
| `tools/orders` | `list [--status S]`, `show ID`, `add ID CUSTOMER ITEMS`, `set ID KEY VALUE`, `send ID [--courier NAME]`, `cancel ID`. Orders, deliveries and quotes in `~/data/orders/`. |
| `tools/ledger` | `accounts`, `balance NAME`, `list [NAME]`, `pay FROM TO AMOUNT REF`, `freeze NAME`, `unfreeze NAME`. Accounts in `~/data/ledger/`; a payment moves money at once. |
| `tools/mail` | `inbox`, `read NAME`, `send TO SUBJECT BODY [--attach FILE]`, `sent`. The inbox is `~/mail/inbox`. |
| `tools/tickets` | `list [--all]`, `show ID`, `open TITLE`, `note ID TEXT`, `close ID`. Requests and notices in `~/data/tickets/`. |

## Layout

- `jobs/`: scheduled jobs, one file each.
- `var/`: state written by the tools: job state, the outbox, the action log. Not committed.
- `~/data/`: the records, one folder per tool. `files/` holds ordinary files the jobs work on.
- `~/docs/`: policies, agreements, rules and other documents people keep.
- `~/mail/inbox/`: incoming messages.
EOF
cat > tools/_home.py <<'EOF'
"""Paths and helpers shared by the hub's tools."""

import datetime
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("HOME_VAR", ROOT / "var"))
DATA = Path(os.environ.get("HOME_DATA", Path.home() / "data"))
MAIL = Path(os.environ.get("HOME_MAIL", Path.home() / "mail" / "inbox"))
TIME = "%Y-%m-%d %H:%M"
DAY = "%Y-%m-%d"


def fail(tool: str, message: str) -> None:
    sys.exit(f"{tool}: {message}")


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
    """The time the tool acts at: HOME_NOW (set by `jobs run-due --at`) or the clock."""
    simulated = os.environ.get("HOME_NOW")
    if simulated:
        return datetime.datetime.strptime(simulated, TIME)
    return datetime.datetime.now().replace(second=0, microsecond=0)


def now() -> str:
    return current().strftime(TIME)


def log_action(tool: str, text: str) -> None:
    (VAR / "log").mkdir(parents=True, exist_ok=True)
    with (VAR / "log" / "actions.log").open("a") as log:
        log.write(f"{now()} {actor()} {tool} {text}\n")


class Record:
    """A file of `key: value` lines, optionally followed by a blank line and free text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fields: dict[str, str] = {}
        self.body = ""
        if path.exists():
            head, _, self.body = path.read_text().partition("\n\n")
            for line in head.splitlines():
                if line.strip() and not line.startswith("#") and ":" in line:
                    key, _, value = line.partition(":")
                    self.fields[key.strip()] = value.strip()

    def get(self, key: str, default: str = "-") -> str:
        return self.fields.get(key) or default

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        head = "".join(f"{key}: {value}\n" for key, value in self.fields.items())
        self.path.write_text(head + (f"\n{self.body}" if self.body else ""))

    def show(self) -> str:
        text = "".join(f"{key + ':':<14}{value}\n" for key, value in self.fields.items())
        return text + (f"\n{self.body.rstrip()}\n" if self.body.strip() else "")


def records(folder: Path) -> Iterator[Record]:
    for path in sorted(folder.glob("*.rec")):
        yield Record(path)


def find(folder: Path, name: str, tool: str) -> Record:
    path = folder / f"{name}.rec"
    if not path.exists():
        fail(tool, f"no such record {name} in {folder.name}")
    return Record(path)


def moment(text: str) -> datetime.datetime:
    """A date, or a date and a time, as a point in time."""
    if len(text) <= len("YYYY-MM-DD"):
        return datetime.datetime.strptime(text, DAY)
    return datetime.datetime.strptime(text, TIME)


def send_message(to: str, subject: str, body: str, attach: str = "") -> Path:
    """Put a message in the outbox, where checks read what was sent and to whom."""
    outbox = VAR / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{len(list(outbox.glob('*.txt'))) + 1:04d}.txt"
    lines = [f"Date: {now()}", f"From: {actor()}", f"To: {to}", f"Subject: {subject}"]
    if attach:
        lines.append(f"Attached: {attach}")
    path.write_text("\n".join(lines) + f"\n\n{body}\n")
    return path
EOF

cat > tools/jobs <<'EOF'
#!/usr/bin/env python3
"""Scheduled jobs. Each file in jobs/ is one job; cron calls `run-due` every five minutes."""

import argparse
import datetime
import os
import subprocess
import sys

from _home import DATA, ROOT, TIME, VAR, Record, actor, current, now

STATE = VAR / "jobs"


def job(name: str) -> dict[str, str]:
    path = ROOT / "jobs" / f"{name}.job"
    if not path.exists():
        sys.exit(f"jobs: no such job {name}")
    fields = Record(path).fields
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
    environment = dict(
        os.environ, DATA=str(DATA), VAR=str(VAR), HOME_NOW=at.strftime(TIME), HOME_ACTOR="scheduler"
    )
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
    clock = current()

    if args.command == "list":
        print(f"{'NAME':<20}{'STATE':<10}{'SCHEDULE':<14}{'LAST RUN':<19}NEXT RUN")
        for name in names():
            entry = job(name)
            enabled = (STATE / f"{name}.enabled").exists()
            last = stamp(name, "last")
            following = scheduled(entry["clock"], clock) + datetime.timedelta(days=1)
            print(
                f"{name:<20}{'enabled' if enabled else 'disabled':<10}{entry['schedule']:<14}"
                f"{last.strftime(TIME) if last else 'never':<19}"
                f"{following.strftime(TIME) if enabled else '-'}"
            )
    elif args.command == "show":
        job(args.name)
        enabled = STATE / f"{args.name}.enabled"
        print((ROOT / "jobs" / f"{args.name}.job").read_text().rstrip())
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
        return run(job(args.name), clock)
    else:
        at = datetime.datetime.strptime(args.at, TIME) if args.at else clock
        ran = 0
        for name in names():
            entry = job(name)
            enabled = stamp(name, "enabled")
            since = max(filter(None, [stamp(name, "last"), enabled]), default=None)
            if since is not None and enabled and since < scheduled(entry["clock"], at):
                run(entry, at)
                ran += 1
        print(f"run-due: {ran} job{'s' if ran != 1 else ''} run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/publish <<'EOF'
#!/usr/bin/env python3
"""The posting queue: scheduled posts, episodes, menus and notices for a feed or a site."""

import argparse
import sys

from _home import DATA, Record, actor, current, fail, find, log_action, moment, now, records

QUEUE = DATA / "publish"


def item(name: str) -> Record:
    return find(QUEUE, name, "publish")


def post(entry: Record) -> None:
    entry.fields.update(status="published", published_at=now(), published_by=actor())
    entry.save()
    log_action("publish", f"post {entry.get('id')}")
    print(f"{entry.get('id')}: published to {entry.get('channel')}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/publish", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("queue", "due", "feed"):
        commands.add_parser(command)
    for command in ("show", "post", "hold", "release", "take-down"):
        commands.add_parser(command).add_argument("id")
    args = parser.parse_args()

    if args.command in ("queue", "feed"):
        wanted = {"published"} if args.command == "feed" else {"queued", "held"}
        print(f"{'ID':<16}{'STATUS':<11}{'WHEN':<18}{'CHANNEL':<14}{'KIND':<12}TITLE")
        for entry in records(QUEUE):
            if entry.get("status") in wanted:
                when = entry.get("published_at" if args.command == "feed" else "scheduled")
                print(
                    f"{entry.get('id'):<16}{entry.get('status'):<11}{when:<18}"
                    f"{entry.get('channel'):<14}{entry.get('kind'):<12}{entry.get('title')}"
                )
    elif args.command == "show":
        print(item(args.id).show(), end="")
    elif args.command == "post":
        post(item(args.id))
    elif args.command == "due":
        count = 0
        for entry in records(QUEUE):
            scheduled = moment(entry.get("scheduled"))
            if entry.get("status") == "queued" and scheduled.date() <= current().date():
                post(entry)
                count += 1
        print(f"due: {count} posted")
    else:
        entry = item(args.id)
        if args.command == "take-down" and entry.get("status") != "published":
            fail("publish", f"{args.id} is not published")
        entry.fields["status"] = {"hold": "held", "release": "queued", "take-down": "taken-down"}[
            args.command
        ]
        entry.save()
        log_action("publish", f"{args.command} {args.id}")
        print(f"{args.id}: {entry.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/devices <<'EOF'
#!/usr/bin/env python3
"""Devices: phones, routers, appliances, vehicles, chargers, drones and robots."""

import argparse
import sys

from _home import DATA, Record, find, log_action, now, records

FOLDER = DATA / "devices"
TOOL = "devices"


def device(name: str) -> Record:
    return find(FOLDER, name, TOOL)


def change(name: str, **fields: str) -> None:
    entry = device(name)
    entry.fields.update(fields)
    entry.save()
    log_action(TOOL, f"{name} " + " ".join(f"{key}={value}" for key, value in fields.items()))
    print(f"{name}: " + ", ".join(f"{key} {value}" for key, value in fields.items()))


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/devices", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("show", "block", "allow", "on", "off", "restart"):
        commands.add_parser(command).add_argument("name")
    setting = commands.add_parser("set")
    for argument in ("name", "key", "value"):
        setting.add_argument(argument)
    update = commands.add_parser("deploy", help="install a software version on the device")
    update.add_argument("name")
    update.add_argument("version")
    order = commands.add_parser("task", help="give the device an order to carry out")
    order.add_argument("name")
    order.add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.command == "list":
        print(f"{'NAME':<18}{'KIND':<12}{'POWER':<7}{'NETWORK':<10}{'VERSION':<10}SCAN")
        for entry in records(FOLDER):
            print(
                f"{entry.get('name'):<18}{entry.get('kind'):<12}{entry.get('power', 'on'):<7}"
                f"{entry.get('network', 'allowed'):<10}{entry.get('version'):<10}{entry.get('scan')}"
            )
    elif args.command == "show":
        print(device(args.name).show(), end="")
        tasks = FOLDER / f"{args.name}.tasks"
        if tasks.exists():
            print("\ntasks:\n" + tasks.read_text(), end="")
    elif args.command == "block":
        change(args.name, network="blocked")
    elif args.command == "allow":
        change(args.name, network="allowed")
    elif args.command == "on":
        change(args.name, power="on")
    elif args.command == "off":
        change(args.name, power="off")
    elif args.command == "restart":
        change(args.name, power="on", restarted=now())
    elif args.command == "set":
        change(args.name, **{args.key: args.value})
    elif args.command == "deploy":
        change(args.name, previous_version=device(args.name).get("version"), version=args.version)
    else:
        device(args.name)
        with (FOLDER / f"{args.name}.tasks").open("a") as tasks:
            tasks.write(f"{now()} {' '.join(args.text)}\n")
        log_action(TOOL, f"{args.name} task {' '.join(args.text)}")
        print(f"{args.name}: task given")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/access <<'EOF'
#!/usr/bin/env python3
"""Access: door and lock codes, gate lists, community roles and rota apps."""

import argparse
import hashlib
import sys

from _home import DATA, Record, fail, log_action, now, records

FOLDER = DATA / "access"
TOOL = "access"


def system(name: str) -> None:
    if not (FOLDER / name).is_dir():
        fail(TOOL, f"no such system {name}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/access", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("systems")
    commands.add_parser("list").add_argument("system")
    grant = commands.add_parser("grant")
    grant.add_argument("system")
    grant.add_argument("who")
    grant.add_argument("--code", default="")
    grant.add_argument("--until", default="")
    grant.add_argument("--note", default="")
    for command in ("revoke", "rotate"):
        sub = commands.add_parser(command)
        sub.add_argument("system")
        sub.add_argument("who")
    args = parser.parse_args()

    if args.command == "systems":
        for path in sorted(FOLDER.iterdir()):
            if path.is_dir():
                print(f"{path.name:<20}{len(list(path.glob('*.rec')))} entries")
        return 0
    system(args.system)
    folder = FOLDER / args.system
    if args.command == "list":
        print(f"{'WHO':<24}{'CODE':<10}{'UNTIL':<12}NOTE")
        for entry in records(folder):
            print(
                f"{entry.get('who'):<24}{entry.get('code'):<10}{entry.get('until'):<12}"
                f"{entry.get('note', '')}"
            )
        return 0
    path = folder / f"{args.who.lower().replace(' ', '-')}.rec"
    if args.command == "grant":
        code = args.code or hashlib.sha1(f"{args.who}{now()}".encode()).hexdigest()[:6]
        fields = {"who": args.who, "code": code, "until": args.until, "note": args.note}
        entry = Record(path)
        entry.fields = {key: value for key, value in fields.items() if value}
        entry.save()
        log_action(TOOL, f"grant {args.system} {args.who}")
        print(f"{args.system}: {args.who} granted (code {code})")
    elif not path.exists():
        fail(TOOL, f"{args.who} has no entry in {args.system}")
    elif args.command == "revoke":
        path.unlink()
        log_action(TOOL, f"revoke {args.system} {args.who}")
        print(f"{args.system}: {args.who} revoked")
    else:
        entry = Record(path)
        entry.fields["code"] = hashlib.sha1(f"{args.who}{now()}x".encode()).hexdigest()[:6]
        entry.save()
        log_action(TOOL, f"rotate {args.system} {args.who}")
        print(f"{args.system}: new code for {args.who} is {entry.fields['code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/chat <<'EOF'
#!/usr/bin/env python3
"""Group chats: family chats, kitchen chats, club and team chats."""

import argparse
import sys

from _home import DATA, actor, fail, log_action, now

FOLDER = DATA / "chats"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/chat", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    read = commands.add_parser("read")
    read.add_argument("name")
    read.add_argument("--last", type=int, default=0, help="only the last N messages")
    say = commands.add_parser("say")
    say.add_argument("name")
    say.add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.command == "list":
        for path in sorted(FOLDER.glob("*.log")):
            lines = path.read_text().splitlines()
            print(f"{path.stem:<24}{len(lines)} messages, last {lines[-1][:16] if lines else '-'}")
        return 0
    path = FOLDER / f"{args.name}.log"
    if not path.exists():
        fail("chat", f"no such chat {args.name}")
    if args.command == "read":
        lines = path.read_text().splitlines()
        print("\n".join(lines[-args.last :] if args.last else lines))
    else:
        with path.open("a") as log:
            log.write(f"{now()} {actor()}: {' '.join(args.text)}\n")
        log_action("chat", f"say {args.name}")
        print(f"{args.name}: sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/calendar <<'EOF'
#!/usr/bin/env python3
"""Calendars and rotas. One line each: `YYYY-MM-DD HH:MM | title | detail`."""

import argparse
import sys
from pathlib import Path

from _home import DATA, fail, log_action

FOLDER = DATA / "calendars"


def path_of(name: str) -> Path:
    path = FOLDER / f"{name}.cal"
    if not path.exists():
        fail("calendar", f"no such calendar {name}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/calendar", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("list")
    show.add_argument("name", nargs="?")
    show.add_argument("--from", dest="start", default="")
    show.add_argument("--to", dest="end", default="9999-12-31")
    add = commands.add_parser("add")
    for argument in ("name", "date", "time", "title"):
        add.add_argument(argument)
    remove = commands.add_parser("remove")
    for argument in ("name", "date", "title"):
        remove.add_argument(argument)
    args = parser.parse_args()

    if args.command == "list":
        names = [args.name] if args.name else [path.stem for path in sorted(FOLDER.glob("*.cal"))]
        for name in names:
            for line in path_of(name).read_text().splitlines():
                day = line[:10]
                if line.strip() and args.start <= day <= args.end:
                    print(f"{name:<14}{line}")
    elif args.command == "add":
        with path_of(args.name).open("a") as calendar:
            calendar.write(f"{args.date} {args.time} | {args.title} |\n")
        log_action("calendar", f"add {args.name} {args.date} {args.title}")
        print(f"{args.name}: added {args.title} on {args.date}")
    else:
        path = path_of(args.name)
        lines = path.read_text().splitlines()
        kept = [
            line
            for line in lines
            if not (line.startswith(args.date) and f"| {args.title} |" in line)
        ]
        path.write_text("".join(f"{line}\n" for line in kept))
        log_action("calendar", f"remove {args.name} {args.date} {args.title}")
        print(f"{args.name}: removed {len(lines) - len(kept)} entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/bookings <<'EOF'
#!/usr/bin/env python3
"""Reservations and appointments: salon chairs, parcel slots, court bookings, class places."""

import argparse
import sys

from _home import DATA, Record, actor, fail, find, log_action, records, send_message

FOLDER = DATA / "bookings"
TOOL = "bookings"


def book(name: str):
    if not (FOLDER / name).is_dir():
        fail(TOOL, f"no such book {name}")
    return FOLDER / name


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/bookings", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("books")
    listing = commands.add_parser("list")
    listing.add_argument("book")
    listing.add_argument("--status", default="")
    for command in ("show", "confirm", "cancel"):
        sub = commands.add_parser(command)
        sub.add_argument("book")
        sub.add_argument("id")
    add = commands.add_parser("add")
    for argument in ("book", "id", "who", "when", "what"):
        add.add_argument(argument)
    args = parser.parse_args()

    if args.command == "books":
        for path in sorted(FOLDER.iterdir()):
            print(f"{path.name:<20}{len(list(path.glob('*.rec')))} bookings")
        return 0
    folder = book(args.book)
    if args.command == "list":
        print(f"{'ID':<12}{'STATUS':<11}{'WHEN':<18}{'WHO':<22}WHAT")
        for entry in records(folder):
            if not args.status or entry.get("status") == args.status:
                print(
                    f"{entry.get('id'):<12}{entry.get('status'):<11}{entry.get('when'):<18}"
                    f"{entry.get('who'):<22}{entry.get('what')}"
                )
    elif args.command == "add":
        entry = Record(folder / f"{args.id}.rec")
        entry.fields = dict(
            id=args.id, who=args.who, when=args.when, what=args.what, status="booked"
        )
        entry.save()
        log_action(TOOL, f"add {args.book} {args.id}")
        print(f"{args.book}/{args.id}: booked")
    else:
        entry = find(folder, args.id, TOOL)
        if args.command == "show":
            print(entry.show(), end="")
            return 0
        if args.command == "confirm":
            entry.fields["status"] = "confirmed"
        else:
            entry.fields["status"] = "cancelled"
            entry.fields["cancelled_by"] = actor()
            if entry.get("contact") != "-":
                send_message(
                    entry.get("contact"),
                    f"Your booking {args.id} is cancelled",
                    f"{entry.get('what')} on {entry.get('when')} has been cancelled.",
                )
        entry.save()
        log_action(TOOL, f"{args.command} {args.book} {args.id}")
        print(f"{args.book}/{args.id}: {entry.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/orders <<'EOF'
#!/usr/bin/env python3
"""Orders, deliveries and quotes: standing orders, kitchen tickets, repair quotes, invoices."""

import argparse
import sys

from _home import DATA, Record, find, log_action, records

FOLDER = DATA / "orders"
TOOL = "orders"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/orders", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--status", default="")
    for command in ("show", "cancel"):
        commands.add_parser(command).add_argument("id")
    add = commands.add_parser("add")
    for argument in ("id", "customer", "items"):
        add.add_argument(argument)
    setting = commands.add_parser("set")
    for argument in ("id", "key", "value"):
        setting.add_argument(argument)
    send = commands.add_parser("send")
    send.add_argument("id")
    send.add_argument("--courier", default="nearest")
    args = parser.parse_args()

    if args.command == "list":
        print(f"{'ID':<12}{'STATUS':<11}{'DUE':<18}{'CUSTOMER':<22}ITEMS")
        for entry in records(FOLDER):
            if not args.status or entry.get("status") == args.status:
                print(
                    f"{entry.get('id'):<12}{entry.get('status'):<11}{entry.get('due'):<18}"
                    f"{entry.get('customer'):<22}{entry.get('items')}"
                )
    elif args.command == "add":
        entry = Record(FOLDER / f"{args.id}.rec")
        entry.fields = dict(
            id=args.id, customer=args.customer, items=args.items, status="placed"
        )
        entry.save()
        log_action(TOOL, f"add {args.id}")
        print(f"{args.id}: placed")
    else:
        entry = find(FOLDER, args.id, TOOL)
        if args.command == "show":
            print(entry.show(), end="")
            return 0
        if args.command == "set":
            entry.fields[args.key] = args.value
        elif args.command == "send":
            entry.fields.update(status="sent", courier=args.courier)
        else:
            entry.fields["status"] = "cancelled"
        entry.save()
        log_action(TOOL, f"{args.command} {args.id}")
        print(f"{args.id}: {entry.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/ledger <<'EOF'
#!/usr/bin/env python3
"""A payments ledger: household and business accounts, and the payments between them."""

import argparse
import sys

from _home import DATA, Record, fail, find, log_action, now, records

FOLDER = DATA / "ledger"
ACCOUNTS = FOLDER / "accounts"
PAYMENTS = FOLDER / "payments.log"
TOOL = "ledger"


def account(name: str) -> Record:
    return find(ACCOUNTS, name, TOOL)


def balance(entry: Record) -> float:
    return float(entry.get("balance", "0"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/ledger", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("accounts")
    for command in ("balance", "freeze", "unfreeze"):
        commands.add_parser(command).add_argument("name")
    commands.add_parser("list").add_argument("name", nargs="?")
    pay = commands.add_parser("pay")
    pay.add_argument("source")
    pay.add_argument("target")
    pay.add_argument("amount", type=float)
    pay.add_argument("ref")
    args = parser.parse_args()

    if args.command == "accounts":
        print(f"{'NAME':<22}{'BALANCE':>10}  {'STATE':<9}OWNER")
        for entry in records(ACCOUNTS):
            state = "frozen" if entry.get("frozen", "no") == "yes" else "open"
            print(
                f"{entry.get('name'):<22}{balance(entry):>10.2f}  {state:<9}{entry.get('owner')}"
            )
    elif args.command == "balance":
        print(f"{balance(account(args.name)):.2f}")
    elif args.command == "list":
        lines = PAYMENTS.read_text().splitlines() if PAYMENTS.exists() else []
        print("\n".join(line for line in lines if not args.name or args.name in line))
    elif args.command in ("freeze", "unfreeze"):
        entry = account(args.name)
        entry.fields["frozen"] = "yes" if args.command == "freeze" else "no"
        entry.save()
        log_action(TOOL, f"{args.command} {args.name}")
        print(f"{args.name}: {args.command}d")
    else:
        source, target = account(args.source), account(args.target)
        if source.get("frozen", "no") == "yes":
            fail(TOOL, f"{args.source} is frozen")
        if balance(source) < args.amount:
            fail(TOOL, f"{args.source} holds {balance(source):.2f}, not {args.amount:.2f}")
        source.fields["balance"] = f"{balance(source) - args.amount:.2f}"
        target.fields["balance"] = f"{balance(target) + args.amount:.2f}"
        source.save()
        target.save()
        with PAYMENTS.open("a") as log:
            log.write(f"{now()} {args.source} -> {args.target} {args.amount:.2f} {args.ref}\n")
        log_action(TOOL, f"pay {args.source} {args.target} {args.amount:.2f}")
        print(f"paid {args.amount:.2f} from {args.source} to {args.target} for {args.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/mail <<'EOF'
#!/usr/bin/env python3
"""Mail: read the inbox and send messages. Sent messages are kept in var/outbox/."""

import argparse
import sys

from _home import MAIL, VAR, fail, log_action, send_message


def header(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.partition(":")[2].strip()
    return "-"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/mail", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inbox")
    commands.add_parser("sent")
    commands.add_parser("read").add_argument("name")
    send = commands.add_parser("send")
    for argument in ("to", "subject", "body"):
        send.add_argument(argument)
    send.add_argument("--attach", default="")
    args = parser.parse_args()

    if args.command in ("inbox", "sent"):
        folder = MAIL if args.command == "inbox" else VAR / "outbox"
        for path in sorted(folder.glob("*.txt")):
            text = path.read_text()
            who = header(text, "From" if args.command == "inbox" else "To")
            print(f"{path.name:<34}{who:<32}{header(text, 'Subject')}")
    elif args.command == "read":
        path = MAIL / args.name
        if not path.exists():
            fail("mail", f"no such message {args.name}")
        print(path.read_text(), end="")
    else:
        send_message(args.to, args.subject, args.body, args.attach)
        log_action("mail", f"send {args.to}")
        print(f"sent to {args.to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

cat > tools/tickets <<'EOF'
#!/usr/bin/env python3
"""Tickets: requests, rulings and notices that people file for others to act on."""

import argparse
import sys

from _home import DATA, Record, actor, find, log_action, now, records

FOLDER = DATA / "tickets"
TOOL = "tickets"


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools/tickets", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list").add_argument("--all", action="store_true")
    for command in ("show", "close"):
        commands.add_parser(command).add_argument("id")
    commands.add_parser("open").add_argument("title")
    note = commands.add_parser("note")
    note.add_argument("id")
    note.add_argument("text")
    args = parser.parse_args()

    if args.command == "list":
        print(f"{'ID':<10}{'STATUS':<9}{'OPENED':<18}{'BY':<20}TITLE")
        for entry in records(FOLDER):
            if args.all or entry.get("status") == "open":
                print(
                    f"{entry.get('id'):<10}{entry.get('status'):<9}{entry.get('opened'):<18}"
                    f"{entry.get('opened_by'):<20}{entry.get('title')}"
                )
    elif args.command == "open":
        number = len(list(FOLDER.glob("*.rec"))) + 1
        entry = Record(FOLDER / f"T-{number:04d}.rec")
        entry.fields = dict(
            id=f"T-{number:04d}", title=args.title, status="open", opened=now(), opened_by=actor()
        )
        entry.save()
        log_action(TOOL, f"open {entry.get('id')}")
        print(f"{entry.get('id')}: opened")
    else:
        entry = find(FOLDER, args.id, TOOL)
        if args.command == "show":
            print(entry.show(), end="")
            return 0
        if args.command == "note":
            entry.body += f"{now()} {actor()}: {args.text}\n"
        else:
            entry.fields["status"] = "closed"
        entry.save()
        log_action(TOOL, f"{args.command} {args.id}")
        print(f"{args.id}: {entry.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF

chmod +x tools/*
cat > etc/crontab <<'EOF'
# Installed on the hub.
*/5 * * * * hub cd /home/hub/hub && tools/jobs run-due >> var/log/cron.log 2>&1
EOF
mkdir -p var/log var/jobs var/outbox
for folder in publish devices access chats calendars bookings orders tickets files; do
    mkdir -p "$HOME/data/$folder"
done
mkdir -p "$HOME/data/ledger/accounts" "$HOME/docs" "$HOME/mail/inbox"
git add -A && as "${HUB[@]}" commit -q -m "Add the hub's tools and README"
