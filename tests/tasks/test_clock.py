"""One calendar day for the harness's scripts across a session."""

import subprocess
from datetime import UTC, date, datetime, timedelta

from steward.clock import SessionClock, posix_zone
from steward.days import day_of

CLOCK = SessionClock(day=date(2026, 11, 25), offset=timedelta(hours=1))


def test_the_container_clock_is_read_from_what_date_prints() -> None:
    assert SessionClock.parse("2026-11-25 +0100\n") == CLOCK
    west = SessionClock.parse("2026-11-25 -0530")
    assert west is not None and west.offset == -timedelta(hours=5, minutes=30)
    assert SessionClock.parse("") is None


def test_before_midnight_the_container_zone_is_kept() -> None:
    assert CLOCK.zone(datetime(2026, 11, 25, 12, 0, tzinfo=UTC)) == "STW-01:00:00"


def test_after_midnight_the_session_day_is_held() -> None:
    # 00:30 local on the 26th, 23:30 UTC on the 25th: held at 23:59 on the 25th.
    zone = CLOCK.zone(datetime(2026, 11, 25, 23, 30, tzinfo=UTC))
    assert zone == "STW-00:29:00"
    printed = subprocess.run(
        [
            "date",
            "-d",
            "@" + str(int(datetime(2026, 11, 25, 23, 30, tzinfo=UTC).timestamp())),
            "+%F %H:%M",
        ],
        capture_output=True,
        text=True,
        env={"TZ": zone},
        check=True,
    ).stdout.strip()
    assert printed == "2026-11-25 23:59"


def test_a_day_past_the_session_cannot_be_held() -> None:
    assert CLOCK.zone(datetime(2026, 11, 27, 12, 0, tzinfo=UTC)) is None


def test_posix_zones_count_west_as_positive() -> None:
    assert posix_zone(timedelta(hours=2)) == "STW-02:00:00"
    assert posix_zone(-timedelta(minutes=90, seconds=5)) == "STW+01:30:05"


def shown(zone: str | None, now: datetime) -> str:
    assert zone is not None
    printed = subprocess.run(
        ["date", "-d", f"@{int(now.timestamp())}", "+%F %H:%M"],
        capture_output=True,
        text=True,
        env={"TZ": zone},
        check=True,
    )
    return printed.stdout.strip()


def test_day_two_shows_the_next_date_at_the_same_time_of_day() -> None:
    central = SessionClock(day=date(2026, 11, 25), offset=-timedelta(hours=6))
    now = datetime(2026, 11, 25, 16, 0, tzinfo=UTC)
    assert shown(central.zone(now), now) == "2026-11-25 10:00"
    assert shown(central.zone(now, day=2), now) == "2026-11-26 10:00"


def test_day_two_is_shown_even_where_a_whole_day_cannot_be_added() -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert shown(CLOCK.zone(now, day=2), now) == "2026-11-26 11:59"


def test_past_midnight_each_day_is_held_at_its_own_date() -> None:
    central = SessionClock(day=date(2026, 11, 25), offset=-timedelta(hours=6))
    now = datetime(2026, 11, 26, 7, 0, tzinfo=UTC)
    assert shown(central.zone(now), now) == "2026-11-25 23:59"
    assert shown(central.zone(now, day=2), now) == "2026-11-26 23:59"


def test_every_shift_of_both_days_gets_a_zone_postgres_accepts() -> None:
    # PostgreSQL takes a zone whose offset has seconds for one with leap seconds and refuses
    # the connection. Shifts start at odd seconds, and after midnight the time is held.
    central = SessionClock(day=date(2026, 11, 25), offset=-timedelta(hours=6))
    start = datetime(2026, 11, 26, 4, 17, 23, 456789, tzinfo=UTC)
    for shift in range(1, 13):
        day = day_of(6, shift)
        now = start + (shift - 1) * timedelta(minutes=13, seconds=47, microseconds=1234)
        zone = central.zone(now, day=day)
        assert zone is not None and zone.endswith(":00"), (shift, zone)
        assert shown(zone, now).startswith(f"2026-11-{24 + day} "), (shift, zone)
