from datetime import date, datetime, timedelta
lazy from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

lazy from ofmhelpers.db.jobs.schema import SessionSpan
lazy from ofmhelpers.db.users.schema import User


class DayHours(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    day: date
    seconds: float


def _end(span: SessionSpan, last_seen_at: float | None) -> float:
    if span.end_at is not None:
        return span.end_at
    if span.next_at is None and last_seen_at is not None:
        return max(span.start_at, last_seen_at)
    return max(span.start_at, span.last_job_at or span.start_at)


def _split(user_id: int, start: float, end: float, zone: ZoneInfo) -> list[DayHours]:
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
    minutes = int(seconds // 60)
    if minutes <= 0:
        return "—"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} m" if hours else f"{minutes} m"
