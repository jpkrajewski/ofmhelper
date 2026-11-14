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

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.auth.access import Area
from ofmhelpers.features.accounts import page
from ofmhelpers.features.accounts.removal import remove_accounts
from ofmhelpers.features.accounts.tracker import sync_now
from ofmhelpers.schemas import (
    AccountQuery,
    AddAccounts,
    AddResult,
    AssignCreator,
    Notes,
    SelectedAccounts,
)
from ofmhelpers.web.dependencies import Access, OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/accounts", tags=["accounts"], dependencies=[Access(Area.ACCOUNTS)]
)

ListQuery = Annotated[AccountQuery, Query()]


class _Count(BaseModel):
    """How many accounts a bulk action touched."""

    count: int


@router.get("")
def index(request: Request, query: ListQuery, scope: Scope) -> Response:
    view = page.list_view(query, scope)
    return render(request, "accounts.html", query=query, **dict(view))


@router.post("/add")
def add(body: AddAccounts, owner_id: OwnerId) -> AddResult:
    """Tracks every handle on its own line and queues the first scan of the
    new ones. One bad line rejects the lot, before anything is added."""
    return page.add(body, owner_id)


@router.post("/sync")
def sync(owner_id: OwnerId) -> _Count:
    """Every account of the user scanned now (features/accounts/tracker.py
    `sync_now`); one already syncing is not queued twice."""
    return _Count(count=sync_now(owner_id))


@router.post("/creator")
def creator(body: AssignCreator, scope: Scope) -> _Count:
    """Assigns the accounts to a creator ("" = none), creating it on first
    use."""
    return _Count(count=page.assign_creator(body.ids, body.creator, scope))


@router.post("/delete")
def delete(body: SelectedAccounts, scope: Scope) -> _Count:
    """Removes the accounts everywhere (features/accounts/removal.py): tracking
    stops (history kept), their logins leave the phones (archived, password
    kept), their Drive folders go to the trash."""
    return _Count(count=remove_accounts(body.ids, scope))


@router.post("/{account_id}/notes")
def notes(account_id: int, body: Notes, scope: Scope) -> Notes:
    page.set_notes(account_id, body.notes, scope)
    return body


@router.get("/{account_id}")
def analytics(
    request: Request, account_id: int, query: ListQuery, scope: Scope
) -> Response:
    """One account over the period: KPIs, follower chart, top posts.
    `query` is the list's, kept for the way back."""
    view = page.analytics_view(account_id, query, scope)
    return render(request, "account_analytics.html", query=query, **dict(view))
