# Copyright (C) 2026 jpkrajewski

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
lazy from collections.abc import Collection

from ofmhelpers.db.jobs.schema import Spend, SpendPeriod, UserDay
from ofmhelpers.db.jobs.store import days, sessions, spend
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, User
from ofmhelpers.features.vas.hours import day_hours, worded
from ofmhelpers.features.vas.models import (
    Amount,
    Column,
    OwnerSpend,
    Period,
    ShownZone,
    VaDay,
    VaSpend,
    VasView,
)

_WEEK_DAYS = 7
REEL_TASKS = frozenset({"clone", "clone_long"})
_NONE = "—"
_CENT = Decimal("0.01")


def periods(now: datetime) -> list[SpendPeriod]:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {
        Period.TODAY: today,
        Period.WEEK: today - timedelta(days=_WEEK_DAYS - 1),
        Period.MONTH: today.replace(day=1),
    }
    end = (today + timedelta(days=1)).timestamp()
    return [
        SpendPeriod(name=period, from_ts=start.timestamp(), to_ts=end)
        for period, start in starts.items()
    ]


def _vas(reader: int, user_ids: Collection[int], everyone: bool) -> list[User]:  # noqa: FBT001
    found = [
        user
        for user in users.list_users()
        if user.role == Role.VA and (everyone or user.id in user_ids)
    ]
    return sorted(
        (user for user in found if user.id != reader),
        key=lambda user: user.label.lower(),
    )


def _columns(user_id: int, sums: list[Spend]) -> list[Column]:
    mine = {s.period: s for s in sums if s.user_id == user_id}
    return [
        Column(
            period=period,
            amount=Amount.model_validate(mine[period], from_attributes=True)
            if period in mine
            else Amount(),
        )
        for period in Period
    ]


def _row(va: User, sums: list[Spend]) -> VaSpend:
    return VaSpend(
        user_id=va.id,
        name=va.label,
        active=va.active,
        columns=_columns(va.id, sums),
    )


def _totals(rows: list[VaSpend]) -> list[Column]:
    totals = dict.fromkeys(Period, Amount())
    for row in rows:
        for column in row.columns:
            totals[column.period] = totals[column.period].plus(column.amount)
    return [Column(period=period, amount=amount) for period, amount in totals.items()]


def _owners(vas: list[User], rows: list[VaSpend]) -> list[OwnerSpend]:
    names = {user.id: user.label for user in users.list_users()}
    grouped: dict[int | None, list[VaSpend]] = {}
    for va, row in zip(vas, rows, strict=True):
        grouped.setdefault(va.owner_id, []).append(row)
    found = [
        OwnerSpend(owner=names.get(owner_id or 0), vas=rows_of)
        for owner_id, rows_of in grouped.items()
    ]
    return sorted(found, key=lambda g: (g.owner is None, (g.owner or "").lower()))


def _clock(at: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(at, zone).strftime("%H:%M")


def _logged_out(day: UserDay, va: User, zone: ZoneInfo, today: date) -> str:
    outs = [at for at in (day.logout_at, day.idle_logout_at) if at is not None]
    out = max(outs, default=None)
    if day.last_login_at is not None and (out is None or day.last_login_at > out):
        if day.day == today and va.last_seen_at is not None:
            return f"Active {_clock(va.last_seen_at, zone)}"
        return _NONE
    if out is None:
        return _NONE
    return _clock(out, zone) + (" · idle" if out == day.idle_logout_at else "")


def _day(day: UserDay, va: User, zone: ZoneInfo, today: date, worked_s: float) -> VaDay:
    first = day.first_login_at
    return VaDay(
        user_id=va.id,
        name=va.label,
        day=day.day,
        logged_in=_clock(first, zone) if first is not None else _NONE,
        logged_out=_logged_out(day, va, zone, today),
        hours=worded(worked_s),
        reels=day.reels,
        spent=f"${day.usd.quantize(_CENT):,}" if day.usd is not None else _NONE,
    )


def _days(vas: list[User], month: SpendPeriod, at: datetime) -> list[VaDay]:
    zone = at.tzinfo
    if not isinstance(zone, ZoneInfo):
        return []
    by_id = {va.id: (index, va) for index, va in enumerate(vas)}
    found = days(list(by_id), month.from_ts, month.to_ts, zone.key, REEL_TASKS)
    spans = sessions(list(by_id), month.from_ts, month.to_ts)
    worked = {(h.user_id, h.day): h.seconds for h in day_hours(spans, vas, zone)}
    rows = sorted(found, key=lambda d: (-d.day.toordinal(), by_id[d.user_id][0]))
    return [
        _day(
            d,
            by_id[d.user_id][1],
            zone,
            at.date(),
            worked.get((d.user_id, d.day), 0.0),
        )
        for d in rows
    ]


def page_view(
    reader: int,
    user_ids: Collection[int],
    *,
    everyone: bool,
    zone: ShownZone,
    now: datetime | None = None,
) -> VasView:
    at = (now or datetime.now(UTC)).astimezone(ZoneInfo(zone.zone))
    vas = _vas(reader, user_ids, everyone)
    spans = periods(at)
    sums = spend([va.id for va in vas], spans)
    rows = [_row(va, sums) for va in vas]
    month = next(span for span in spans if span.name == Period.MONTH)
    return VasView(
        owners=_owners(vas, rows),
        totals=_totals(rows),
        days=_days(vas, month, at),
        grouped=everyone,
        zone=zone.label,
    )
