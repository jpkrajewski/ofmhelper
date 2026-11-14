"""The time zone a person's pages are in: the header's clock, /vas' days, the
galleries' date filters.

It starts as the person's own: their browser knows the zone of the computer
or phone they sit at (`Intl.DateTimeFormat().resolvedOptions().timeZone`, an
IANA name like "Asia/Manila") and static/js/time-zone.js puts it in the
`ofm_timezone` cookie. No IP lookup: the browser's answer is exact, free and
right behind a VPN. The globe's menu (`ui.time_zone_menu`) changes it:
"Automatic" (theirs again) or one of `TIME_ZONES`, the zones the team works
in; a new one there = one entry.

The server takes any zone its time zone database knows (`time_zone`), so a
person anywhere gets their own days."""

from functools import lru_cache
from zoneinfo import available_timezones

from pydantic import BaseModel


class TimeZone(BaseModel):
    label: str
    zone: str


# What the globe offers next to "Automatic"; the first is used until the
# browser has said its own.
TIME_ZONES = (
    TimeZone(label="Warsaw", zone="Europe/Warsaw"),
    TimeZone(label="Ambala", zone="Asia/Kolkata"),
    TimeZone(label="Manila", zone="Asia/Manila"),
)


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    return frozenset(available_timezones())


def time_zone(chosen: str | None) -> TimeZone:
    """The zone the cookie names: one of the globe's, or any real zone
    under its city's name ("America/New_York" -> "New York"); the first of
    the globe's for none or for text that is no zone."""
    listed = next((tz for tz in TIME_ZONES if tz.zone == chosen), None)
    if listed is not None:
        return listed
    if chosen and chosen in _known():
        return TimeZone(label=chosen.rsplit("/", 1)[-1].replace("_", " "), zone=chosen)
    return TIME_ZONES[0]
