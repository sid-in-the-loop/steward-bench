"""The calendar dates the scripts of a session see.

Item scripts and checks each read the date themselves, so a session that runs past midnight
would build a shift with one date and check it with the next. The session reads the
container's date and UTC offset once, at its start, and every command it runs is given a TZ
under which the local date is the date of the shift's day: the session's first date on day 1,
and in a session whose days are dated, one more for each later day, so that a note written
"until tomorrow" on day 1 has run out on day 2. Shifts of one day share a date. The time of
day is kept where it can be; past the end of the shown date it is held at 23:59, and a time
more than a day from the real one, which POSIX offsets cannot express, is brought as close as
they allow. TZ moves only the calendar: epoch seconds and file times are untouched. Shell
`date`, Python's local time and, through PGTZ, a psql session's `current_date` follow it; a
database server's own processes and a world's tool servers do not.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

from pydantic import BaseModel

# What the container prints for `date '+%F %z'`.
READ_CLOCK = "date '+%F %z'"
PRINTED = re.compile(r"(\d{4}-\d{2}-\d{2}) ([+-])(\d{2})(\d{2})")
# The furthest a POSIX offset reaches: its hours stop short of 25, and a day is kept clear.
LONGEST_OFFSET = timedelta(hours=23, minutes=59)
HELD_AT = time(23, 59)


class SessionClock(BaseModel):
    """The session's day and the container's UTC offset when the session began."""

    day: date
    offset: timedelta

    @classmethod
    def parse(cls, printed: str) -> SessionClock | None:
        found = PRINTED.search(printed)
        if found is None:
            return None
        day, sign, hours, minutes = found.groups()
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        return cls(day=date.fromisoformat(day), offset=offset if sign == "+" else -offset)

    def zone(self, now: datetime | None = None, day: int = 1) -> str | None:
        """The TZ under which the local date at `now` is the date of the session's `day`,
        counted from 1, or None where no offset can show it."""
        utc = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
        later = timedelta(days=day - 1)
        shown = self.day + later
        local = utc + self.offset + later
        if local.date() > shown:
            local = datetime.combine(shown, HELD_AT)
        offset = max(min(local - utc, LONGEST_OFFSET), -LONGEST_OFFSET)
        if (utc + offset).date() != shown:
            return None
        return posix_zone(offset)


def posix_zone(offset: timedelta) -> str:
    """A POSIX TZ value for a fixed offset east of UTC; POSIX counts west as positive."""
    seconds = int(offset.total_seconds())
    sign = "-" if seconds >= 0 else "+"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"STW{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
