from functools import lru_cache
from zoneinfo import available_timezones

from pydantic import BaseModel


class TimeZone(BaseModel):
    label: str
    zone: str


TIME_ZONES = (
    TimeZone(label="Warsaw", zone="Europe/Warsaw"),
    TimeZone(label="Ambala", zone="Asia/Kolkata"),
    TimeZone(label="Manila", zone="Asia/Manila"),
)


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    return frozenset(available_timezones())


def time_zone(chosen: str | None) -> TimeZone:
    listed = next((tz for tz in TIME_ZONES if tz.zone == chosen), None)
    if listed is not None:
        return listed
    if chosen and chosen in _known():
        return TimeZone(label=chosen.rsplit("/", 1)[-1].replace("_", " "), zone=chosen)
    return TIME_ZONES[0]
