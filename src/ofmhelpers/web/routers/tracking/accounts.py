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
account due for its rescan, web/account_tracker.py), so no request waits on
a platform; static/js/accounts.js reloads while a row is still scanning.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.socials import platforms
from ofmhelpers.web.account_removal import remove_accounts
from ofmhelpers.web.account_tracker import queue_due_scans
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import (
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
from ofmhelpers.web.stores.devices import phones_by_account
from ofmhelpers.web.stores.short_links import links_by_account
from ofmhelpers.web.stores.tracked_accounts import (
    assign_creator,
    get_account,
    get_history,
    list_accounts,
    list_creators,
    set_notes,
    track,
)
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _period(query: AccountQuery) -> Period:
    """The query's period when it is a configured one, else the default."""
    s = settings.accounts
    days = query.period if query.period in s.periods_days else s.default_period_days
    return Period(days=days)


def _periods() -> list[Period]:
    return [Period(days=d) for d in settings.accounts.periods_days]


class _Count(BaseModel):
    """How many accounts a bulk action touched."""

    count: int


@router.get("")
def index(request: Request, query: Annotated[AccountQuery, Query()]):
    s = settings.accounts
    scope = AuthMiddleware.scope(request)
    period = _period(query)
    matching = [a for a in list_accounts(scope) if query.matches(a)]
    accounts = query.ordered(matching, period.days)
    page = AccountPage.of(accounts, query.page, query.per_page or s.default_page_size)
    return render(
        request,
        "accounts.html",
        page=page,
        phones=phones_by_account(page.rows, scope),
        links=links_by_account(scope),
        # Link numbers are per UTC day: "today", not "24h", for period 1.
        link_period=LinkPeriod(days=period.days),
        summary=AccountSummary.of(matching, period.days),
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
def add(request: Request, body: AddAccounts) -> AddResult:
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
    user_id = AuthMiddleware.owner_id(request)
    added = [
        h
        for h in handles
        if track(body.platform, h, body.creator or None, user_id=user_id)
    ]
    queue_due_scans()
    return AddResult(added=added, existing=len(handles) - len(added))


@router.post("/creator")
def creator(request: Request, body: AssignCreator) -> _Count:
    """Assigns the accounts to a creator ("" = none), creating it on first
    use."""
    scope = AuthMiddleware.scope(request)
    return _Count(count=assign_creator(body.ids, body.creator or None, scope))


@router.post("/delete")
def delete(request: Request, body: SelectedAccounts) -> _Count:
    """Removes the accounts everywhere (web/account_removal.py): tracking
    stops (history kept), their logins leave the phones (archived, password
    kept), their Drive folders go to the trash."""
    return _Count(count=remove_accounts(body.ids, AuthMiddleware.scope(request)))


@router.post("/{account_id}/notes")
def notes(request: Request, account_id: int, body: Notes) -> Notes:
    if not set_notes(account_id, body.notes, AuthMiddleware.scope(request)):
        raise HTTPException(status_code=404, detail="Account not tracked")
    return body


@router.get("/{account_id}")
def analytics(
    request: Request, account_id: int, query: Annotated[AccountQuery, Query()]
):
    """One account over the period: KPIs, follower chart, top posts.
    `query` is the list's, kept for the way back."""
    account = get_account(account_id, AuthMiddleware.scope(request))
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
        query=query,
        period=period,
        periods=_periods(),
    )
