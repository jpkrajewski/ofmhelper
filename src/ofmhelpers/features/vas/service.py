"""What /vas does behind its route (web/routers/vas.py): what each
VA's runs cost today, in the last 7 days and this month, and each VA's days
this month: when they logged in, when they logged out (by Logout, or by the
app after 2 idle hours: `features/users/idle.py`), how many reels they
finished and what the day cost. The days are one SQL query over the job log
(`db/stores/jobs.py` `days`); a reel is a finished clone (`REEL_TASKS`).

Whose VAs: the reader's own (`Viewer.user_ids` minus themselves); an admin
sees every VA, grouped under its owner. A run is a job that made a provider
request; its cost is the job's `cost_usd` (`features/jobs/cost.py`: reported
by the provider, estimated from the app's prices, or unknown). Days are the
reader's chosen time zone's (`schemas/time_zones.py`); the sums are one SQL
query (`db/stores/jobs.py` `spend`)."""

from collections.abc import Collection
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import days, spend
from ofmhelpers.features.vas.models import (
    Amount,
    Column,
    OwnerSpend,
    Period,
    VaDay,
    VaSpend,
    VasView,
)
from ofmhelpers.schemas import Role, User
from ofmhelpers.schemas.persistence import Spend, SpendPeriod, UserDay
from ofmhelpers.schemas.time_zones import time_zone

_WEEK_DAYS = 7
# What counts as a reel a VA generated: a finished clone, short or long.
REEL_TASKS = frozenset({"clone", "clone_long"})
_NONE = "—"
_CENT = Decimal("0.01")


def periods(now: datetime) -> list[SpendPeriod]:
    """The page's three periods at `now` (in the reader's zone): today since
    midnight, the last 7 days (today and the 6 before), this month since
    the 1st. Each ends at the coming midnight."""
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


def _vas(reader: int, user_ids: Collection[int], everyone: bool) -> list[User]:
    """The VAs on the page, by name: every VA for an admin, else the ones
    the reader sees (their own)."""
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
    """The rows under their VA's owner, owners by name, VAs nobody owns
    last."""
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
    """When the VA left that day. Still in after the day's last sign-in:
    today "Active <their last action>", an earlier day nothing."""
    outs = [at for at in (day.logout_at, day.idle_logout_at) if at is not None]
    out = max(outs, default=None)
    if day.last_login_at is not None and (out is None or day.last_login_at > out):
        if day.day == today and va.last_seen_at is not None:
            return f"Active {_clock(va.last_seen_at, zone)}"
        return _NONE
    if out is None:
        return _NONE
    return _clock(out, zone) + (" · idle" if out == day.idle_logout_at else "")


def _day(day: UserDay, va: User, zone: ZoneInfo, today: date) -> VaDay:
    first = day.first_login_at
    return VaDay(
        user_id=va.id,
        name=va.label,
        day=day.day,
        logged_in=_clock(first, zone) if first is not None else _NONE,
        logged_out=_logged_out(day, va, zone, today),
        reels=day.reels,
        spent=f"${day.usd.quantize(_CENT):,}" if day.usd is not None else _NONE,
    )


def _days(vas: list[User], month: SpendPeriod, at: datetime) -> list[VaDay]:
    """This month's days of `vas`, newest day first, VAs in the page's
    order within a day; `at`: now, in the reader's zone."""
    zone = at.tzinfo
    if not isinstance(zone, ZoneInfo):
        return []
    by_id = {va.id: (index, va) for index, va in enumerate(vas)}
    found = days(list(by_id), month.from_ts, month.to_ts, zone.key, REEL_TASKS)
    rows = sorted(found, key=lambda d: (-d.day.toordinal(), by_id[d.user_id][0]))
    return [_day(d, by_id[d.user_id][1], zone, at.date()) for d in rows]


def page_view(
    reader: int,
    user_ids: Collection[int],
    *,
    everyone: bool,
    zone: str | None,
    now: datetime | None = None,
) -> VasView:
    """`reader`'s page: their VAs (`user_ids`: who they see, themselves
    included), or every VA under its owner for an admin (`everyone`).
    `zone`: the time zone cookie's value; `now`: the moment the periods are
    cut at (default: this one)."""
    chosen = time_zone(zone)
    at = (now or datetime.now(UTC)).astimezone(ZoneInfo(chosen.zone))
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
        zone=chosen.label,
    )
