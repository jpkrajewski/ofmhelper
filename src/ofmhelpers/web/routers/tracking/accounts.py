"""
/accounts: social accounts of any registered platform (`socials.platforms()`)
with their followers, follower growth and views over one reporting period
(`settings.accounts.periods_days`, picked on the page), last sync, a
creator, the phones signed in to it (/phones logins) and its short links'
clicks and CTR (/links); KPI totals over the filtered list on top. Search,
filters, period, sort and paging are the query string
(`schemas.AccountQuery`), so every view is a link. `/accounts/{id}` is one
account's analytics for the same period.

Adding accounts queues their first scan on the worker (with any other
account due for its rescan, features/accounts/tracker.py), so no request waits on
a platform; static/js/accounts.js reloads while a row is still scanning.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.devices import phones_by_account
from ofmhelpers.db.stores.short_links import links_by_account
from ofmhelpers.db.stores.tracked_accounts import (
    assign_creator,
    get_account,
    get_history,
    list_accounts,
    list_creators,
    set_notes,
    track,
)
from ofmhelpers.features.accounts.removal import remove_accounts
from ofmhelpers.features.accounts.tracker import queue_due_scans, sync_now
from ofmhelpers.integrations.socials import platforms
from ofmhelpers.schemas import (
    AccountPage,
    AccountQuery,
    AccountSort,
    AccountSummary,
    AddAccounts,
    AddResult,
    AssignCreator,
    LinkPeriod,
    Notes,
    Period,
    SelectedAccounts,
)
from ofmhelpers.web.dependencies import OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/accounts", tags=["accounts"])

ListQuery = Annotated[AccountQuery, Query()]


def _period(query: AccountQuery) -> Period:
    """The query's period when it is a configured one, else the default."""
    s = settings.accounts
    days = query.period if query.period in s.periods_days else s.default_period_days
    return Period(days=days)


def _periods() -> list[Period]:
    return [Period(days=d) for d in settings.accounts.periods_days]


def _ask_for_cookies(limited: bool, user_id: int) -> bool:
    """The "Upload cookies" banner: a sync read without a login, and the
    user has none saved (one who has gains nothing from uploading again)."""
    return limited and login_cookies.saved(user_id) is None


class _Count(BaseModel):
    """How many accounts a bulk action touched."""

    count: int


@router.get("")
def index(request: Request, query: ListQuery, scope: Scope) -> Response:
    s = settings.accounts
    period = _period(query)
    mine = list_accounts(scope)
    matching = [a for a in mine if query.matches(a)]
    accounts = query.ordered(matching, period.days)
    page = AccountPage.of(accounts, query.page, query.per_page or s.default_page_size)
    summary = AccountSummary.of(matching, period.days)
    return render(
        request,
        "accounts.html",
        page=page,
        phones=phones_by_account(page.rows, scope),
        links=links_by_account(scope),
        # Link numbers are per UTC day: "today", not "24h", for period 1.
        link_period=LinkPeriod(days=period.days),
        summary=summary,
        ask_for_cookies=_ask_for_cookies(summary.limited > 0, scope),
        # Sync waits while any of them still syncs (filtered out or not).
        syncing=any(a.syncing for a in mine),
        query=query,
        period=period,
        periods=_periods(),
        sorts=AccountSort,
        platforms=platforms(),
        creators=list_creators(scope),
        page_sizes=s.page_sizes,
        max_add=s.max_add,
    )


@router.post("/add")
def add(body: AddAccounts, owner_id: OwnerId) -> AddResult:
    """Tracks every handle on its own line and queues the first scan of the
    new ones. One already tracked is only assigned to `creator` (when
    given). One bad line rejects the lot, before anything is added."""
    try:
        handles = body.parsed()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not handles:
        raise HTTPException(status_code=400, detail="Add at least one account")
    if len(handles) > settings.accounts.max_add:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.accounts.max_add} accounts at once",
        )
    added = [
        h
        for h in handles
        if track(body.platform, h, body.creator or None, user_id=owner_id)
    ]
    queue_due_scans()
    return AddResult(added=added, existing=len(handles) - len(added))


@router.post("/sync")
def sync(owner_id: OwnerId) -> _Count:
    """Every account of the user scanned now (features/accounts/tracker.py
    `sync_now`); one already syncing is not queued twice."""
    return _Count(count=sync_now(owner_id))


@router.post("/creator")
def creator(body: AssignCreator, scope: Scope) -> _Count:
    """Assigns the accounts to a creator ("" = none), creating it on first
    use."""
    return _Count(count=assign_creator(body.ids, body.creator or None, scope))


@router.post("/delete")
def delete(body: SelectedAccounts, scope: Scope) -> _Count:
    """Removes the accounts everywhere (features/accounts/removal.py): tracking
    stops (history kept), their logins leave the phones (archived, password
    kept), their Drive folders go to the trash."""
    return _Count(count=remove_accounts(body.ids, scope))


@router.post("/{account_id}/notes")
def notes(account_id: int, body: Notes, scope: Scope) -> Notes:
    if not set_notes(account_id, body.notes, scope):
        raise HTTPException(status_code=404, detail="Account not tracked")
    return body


@router.get("/{account_id}")
def analytics(
    request: Request, account_id: int, query: ListQuery, scope: Scope
) -> Response:
    """One account over the period: KPIs, follower chart, top posts.
    `query` is the list's, kept for the way back."""
    account = get_account(account_id, scope)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not tracked")
    s = settings.accounts
    period = _period(query)
    return render(
        request,
        "account_analytics.html",
        account=account,
        chart=get_history(account_id).follower_chart(period.days, s.rescan_interval_s),
        top=account.top_posts(period.days, s.top_posts),
        ask_for_cookies=_ask_for_cookies(
            bool(account.metrics and account.metrics.limited), scope
        ),
        query=query,
        period=period,
        periods=_periods(),
    )
