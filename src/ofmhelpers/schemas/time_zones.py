"""The time zones the header's clock offers (`ui.time_zone_picker`). The choice
is the `ofm_timezone` cookie; pages that print times follow it. A new zone = one
entry."""

from pydantic import BaseModel


class TimeZone(BaseModel):
    label: str
    zone: str


TIME_ZONES = (
    TimeZone(label="Warsaw", zone="Europe/Warsaw"),
    TimeZone(label="Ambala", zone="Asia/Kolkata"),
)


def time_zone(chosen: str | None) -> TimeZone:
    """The zone the cookie names; the first one without a known choice."""
    return next((tz for tz in TIME_ZONES if tz.zone == chosen), TIME_ZONES[0])
