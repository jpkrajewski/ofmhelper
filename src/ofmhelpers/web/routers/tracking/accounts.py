"""
/accounts: social accounts of any registered platform (`socials.platforms()`)
with their followers, follower growth and views over one reporting period
(`settings.accounts.periods_days`, picked on the page), last sync, a
creator and notes; KPI totals over the filtered list on top. Search,
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
from ofmhelpers.web.account_tracker import queue_due_scans
from ofmhelpers.web.schemas import (
    AccountPage,
    AccountQuery,
    AccountSort,
    AccountSummary,
    AddAccounts,
    AddResult,
    AssignCreator,
    Notes,
    Period,
    SelectedAccounts,
)
from ofmhelpers.web.stores.tracked_accounts import (
    assign_creator,
    get_account,
    get_history,
    list_accounts,
    list_creators,
    set_notes,
    track,
    untrack,
)
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _period(query: AccountQuery) -> Period:
    """The query's period when it is a configured one, else the default."""
    days = settings.accounts.periods_days
    return Period(days=query.period if query.period in days else days[0])


def _periods() -> list[Period]:
    return [Period(days=d) for d in settings.accounts.periods_days]


class _Count(BaseModel):
    """How many accounts a bulk action touched."""

    count: int


@router.get("")
def index(request: Request, query: Annotated[AccountQuery, Query()]):
    s = settings.accounts
    period = _period(query)
    matching = [a for a in list_accounts() if query.matches(a)]
    accounts = query.ordered(matching, period.days)
    return render(
        request,
        "accounts.html",
        page=AccountPage.of(
            accounts, query.page, query.per_page or s.default_page_size
        ),
        summary=AccountSummary.of(matching, period.days),
        query=query,
        period=period,
        periods=_periods(),
        sorts=AccountSort,
        platforms=platforms(),
        creators=list_creators(),
        page_sizes=s.page_sizes,
        max_add=s.max_add,
    )


@router.post("/add")
def add(body: AddAccounts) -> AddResult:
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
    added = [h for h in handles if track(body.platform, h, body.creator or None)]
    queue_due_scans()
    return AddResult(added=added, existing=len(handles) - len(added))


@router.post("/creator")
def creator(body: AssignCreator) -> _Count:
    """Assigns the accounts to a creator ("" = none), creating it on first
    use."""
    return _Count(count=assign_creator(body.ids, body.creator or None))


@router.post("/delete")
def delete(body: SelectedAccounts) -> _Count:
    """Stops tracking the accounts; their history goes with them."""
    return _Count(count=untrack(body.ids))


@router.post("/{account_id}/notes")
def notes(account_id: int, body: Notes) -> Notes:
    if not set_notes(account_id, body.notes):
        raise HTTPException(status_code=404, detail="Account not tracked")
    return body


@router.get("/{account_id}")
def analytics(
    request: Request, account_id: int, query: Annotated[AccountQuery, Query()]
):
    """One account over the period: KPIs, follower trend, top posts; the
    details and the daily history folded away. `query` is the list's, kept
    for the way back."""
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not tracked")
    s = settings.accounts
    period = _period(query)
    return render(
        request,
        "account_analytics.html",
        account=account,
        history=get_history(account_id),
        top=account.top_posts(period.days, s.top_posts),
        query=query,
        period=period,
        periods=_periods(),
    )
