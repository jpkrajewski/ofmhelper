"""How long a VA worked each day (owner 2026-11-15: "i need to see hours
worked"): the sum of their sessions, not first sign-in to last logout, so a
break logged out between two sessions is not paid.

A session runs from a sign-in to what ended it:
- a Logout, or the app's idle logout (dated at their last action, so the two
  idle hours are never counted: `features/users/idle.py`);
- nothing yet (still signed in, or the cookie ran out unseen): their last
  action (`users.last_seen_at`);
- another sign-in (a second device, or a day before idle logouts were
  recorded, 2026-11-14): their last job before it, else nothing.

A session over midnight is split between its days (the reader's zone)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

lazy from ofmhelpers.schemas.persistence import SessionSpan
lazy from ofmhelpers.schemas.users import User


class DayHours(BaseModel):
    """One person's worked seconds on one day."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    day: date
    seconds: float


def _end(span: SessionSpan, last_seen_at: float | None) -> float:
    """When `span` stopped counting; its start when nothing says it went on."""
    if span.end_at is not None:
        return span.end_at
    if span.next_at is None and last_seen_at is not None:
        return max(span.start_at, last_seen_at)
    return max(span.start_at, span.last_job_at or span.start_at)


def _split(user_id: int, start: float, end: float, zone: ZoneInfo) -> list[DayHours]:
    """`start`..`end` cut at each local midnight."""
    found = []
    at = datetime.fromtimestamp(start, zone)
    stop = datetime.fromtimestamp(end, zone)
    while at < stop:
        midnight = datetime.combine(
            at.date() + timedelta(days=1), datetime.min.time(), zone
        )
        upto = min(stop, midnight)
        found.append(
            DayHours(
                user_id=user_id,
                day=at.date(),
                seconds=upto.timestamp() - at.timestamp(),
            )
        )
        at = upto
    return found


def day_hours(
    spans: list[SessionSpan], vas: list[User], zone: ZoneInfo
) -> list[DayHours]:
    """Each person's worked seconds per day, from their sessions `spans`;
    `vas` gives each one's last action."""
    seen = {va.id: va.last_seen_at for va in vas}
    totals: dict[tuple[int, date], float] = {}
    for span in spans:
        end = _end(span, seen.get(span.user_id))
        for part in _split(span.user_id, span.start_at, end, zone):
            key = (part.user_id, part.day)
            totals[key] = totals.get(key, 0.0) + part.seconds
    return [
        DayHours(user_id=user_id, day=day, seconds=seconds)
        for (user_id, day), seconds in totals.items()
    ]


def worded(seconds: float) -> str:
    """ "7 h 42 m", "42 m"; "—" for none."""
    minutes = int(seconds // 60)
    if minutes <= 0:
        return "—"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} m" if hours else f"{minutes} m"
