"""The Miscellaneous base: it builds in an empty directory and each tool does its one job."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from steward.cases.case import DOMAIN_NAMES

BASE = Path(__file__).parents[2] / "cases" / "_bases" / "home.sh"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "home").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    environment = {**os.environ, "HOME": str(tmp_path / "home")}
    for name in ("HOME_VAR", "HOME_DATA", "HOME_NOW", "HOME_ACTOR"):
        environment.pop(name, None)
    subprocess.run(["bash", str(BASE)], cwd=work, env=environment, check=True, capture_output=True)
    return work


def tool(
    home: Path, *command: str, now: str = "2030-03-04 21:00"
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "HOME": str(home.parent / "home"),
        "HOME_VAR": str(home / "var"),
        "HOME_DATA": str(home.parent / "home" / "data"),
        "HOME_ACTOR": "agent",
        "HOME_NOW": now,
    }
    return subprocess.run(
        [str(home / "tools" / command[0]), *command[1:]],
        cwd=home,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_the_domain_is_named() -> None:
    assert DOMAIN_NAMES["misc"] == "Miscellaneous"


def test_the_base_is_valid_bash_and_commits(home: Path) -> None:
    subprocess.run(["bash", "-n", str(BASE)], check=True)
    log = subprocess.run(["git", "log", "--oneline"], cwd=home, capture_output=True, text=True)
    assert len(log.stdout.splitlines()) == 1
    assert (home / "README.md").exists()


@pytest.mark.parametrize("name", ["devices", "bookings", "pay", "messages", "calendar", "jobs"])
def test_each_tool_has_help(home: Path, name: str) -> None:
    done = tool(home, name, "--help")
    assert done.returncode == 0
    assert "usage" in done.stdout


def test_devices(home: Path) -> None:
    state = home / "var" / "devices"
    state.mkdir(parents=True)
    (state / "front-door.state").write_text(
        "kind: lock\nstate: locked\nactions: lock=locked, unlock=unlocked\nsettings: autolock\n"
    )
    assert "front-door" in tool(home, "devices", "list").stdout
    assert tool(home, "devices", "run", "front-door", "unlock").returncode == 0
    assert "state: " in tool(home, "devices", "show", "front-door").stdout
    assert "unlocked" in (state / "front-door.state").read_text()
    assert tool(home, "devices", "run", "front-door", "fly").returncode == 1
    assert tool(home, "devices", "set", "front-door", "autolock", "5").returncode == 0
    assert "setting.autolock: 5" in (state / "front-door.state").read_text()
    assert tool(home, "devices", "set", "front-door", "volume", "5").returncode == 1


def test_bookings(home: Path) -> None:
    flags = ("--kind", "rental", "--what", "flat", "--who", "guest")
    made = tool(
        home,
        "bookings",
        "create",
        *flags,
        "--start",
        "2030-03-05 15:00",
        "--end",
        "2030-03-07 10:00",
    )
    assert "BK-0001" in made.stdout
    overlap = tool(
        home,
        "bookings",
        "create",
        *flags,
        "--start",
        "2030-03-06 09:00",
        "--end",
        "2030-03-06 12:00",
    )
    assert overlap.returncode == 1
    assert tool(home, "bookings", "extend", "BK-0001", "--to", "2030-03-08 10:00").returncode == 0
    assert "extended" in tool(home, "bookings", "show", "BK-0001").stdout
    assert tool(home, "bookings", "cancel", "BK-0001").returncode == 0
    assert "cancelled" in tool(home, "bookings", "list", "--status", "cancelled").stdout
    assert tool(home, "bookings", "extend", "BK-0001", "--to", "2030-03-09 10:00").returncode == 1


def test_pay(home: Path) -> None:
    assert "P-0001 paid" in tool(home, "pay", "pay", "florist", "40", "--memo", "deposit").stdout
    scheduled = tool(home, "pay", "schedule", "caterer", "120.5", "--on", "2030-03-04")
    assert "P-0002 scheduled" in scheduled.stdout
    tool(home, "pay", "schedule", "band", "300", "--on", "2030-03-20")
    assert "1 payment " in tool(home, "pay", "run-due").stdout
    assert tool(home, "pay", "refund", "P-0001").returncode == 0
    assert tool(home, "pay", "cancel", "P-0003").returncode == 0
    assert tool(home, "pay", "cancel", "P-0001").returncode == 1
    ledger = (home / "var" / "pay" / "ledger.csv").read_text()
    assert "P-0002,2030-03-04,caterer,120.50,paid" in ledger
    assert "paid in total: 120.50" in tool(home, "pay", "list").stdout


def test_messages(home: Path) -> None:
    threads = home / "var" / "messages"
    threads.mkdir(parents=True)
    (threads / "family.jsonl").write_text(
        json.dumps({"at": "2030-03-04 08:00", "from": "Mum", "text": "Home by six"}) + "\n"
    )
    assert "family" in tool(home, "messages", "threads").stdout
    assert tool(home, "messages", "send", "family", "On my way").returncode == 0
    assert "agent: On my way" in tool(home, "messages", "read", "family", "--last", "1").stdout
    assert "family: On my way" in (home / "var" / "log" / "sent.log").read_text()
    assert tool(home, "messages", "send", "nobody", "hi").returncode == 1


def test_calendar(home: Path) -> None:
    events = home.parent / "home" / "data" / "calendar"
    events.mkdir(parents=True, exist_ok=True)
    (events / "dentist.event").write_text(
        "title: Dentist\nstart: 2030-03-05 09:00\nend: 2030-03-05 10:00\nwho: Sam\n"
    )
    assert "Dentist" in tool(home, "calendar", "list", "--on", "2030-03-05").stdout
    assert "0 events" in tool(home, "calendar", "list", "--who", "Alex").stdout
    assert "title:" in tool(home, "calendar", "show", "dentist").stdout


def test_jobs_run_due_simulates_a_night(home: Path) -> None:
    (home / "jobs" / "nightly.job").write_text(
        "schedule: daily 02:00\ncommand: tools/pay schedule bin 5 --on 2030-03-05\n"
    )
    assert tool(home, "jobs", "enable", "nightly").returncode == 0
    assert "0 jobs run" in tool(home, "jobs", "run-due").stdout
    tonight = tool(home, "jobs", "run-due", "--at", "2030-03-05 02:30")
    assert "1 job run" in tonight.stdout
    assert "P-0001" in (home / "var" / "pay" / "ledger.csv").read_text()
