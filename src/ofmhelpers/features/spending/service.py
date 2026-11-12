"""What /spending does behind its route (web/routers/spending.py): what each
VA's runs cost today, in the last 7 days and this month.

Whose VAs: the reader's own (`Viewer.user_ids` minus themselves); an admin
sees every VA, grouped under its owner. A run is a job that made a provider
request; its cost is the job's `cost_usd` (`features/jobs/cost.py`: reported
by the provider, estimated from the app's prices, or unknown). Days are the
reader's chosen time zone's (`schemas/time_zones.py`); the sums are one SQL
query (`db/stores/jobs.py` `spend`)."""

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import spend
from ofmhelpers.features.spending.models import (
    Amount,
    Column,
    OwnerSpend,
    Period,
    SpendingView,
    VaSpend,
)
from ofmhelpers.schemas import Role, User
from ofmhelpers.schemas.persistence import Spend, SpendPeriod
from ofmhelpers.schemas.time_zones import time_zone

_WEEK_DAYS = 7


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


def page_view(
    reader: int,
    user_ids: Collection[int],
    *,
    everyone: bool,
    zone: str | None,
    now: datetime | None = None,
) -> SpendingView:
    """`reader`'s page: their VAs (`user_ids`: who they see, themselves
    included), or every VA under its owner for an admin (`everyone`).
    `zone`: the time zone cookie's value; `now`: the moment the periods are
    cut at (default: this one)."""
    chosen = time_zone(zone)
    at = (now or datetime.now(UTC)).astimezone(ZoneInfo(chosen.zone))
    vas = _vas(reader, user_ids, everyone)
    sums = spend([va.id for va in vas], periods(at))
    rows = [_row(va, sums) for va in vas]
    return SpendingView(
        owners=_owners(vas, rows),
        totals=_totals(rows),
        grouped=everyone,
        zone=chosen.label,
    )
