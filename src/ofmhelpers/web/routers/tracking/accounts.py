from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.db.tracked_accounts.models import AddAccounts
from ofmhelpers.db.tracked_accounts.schema import (
    AccountQuery,
    AddResult,
    AssignCreator,
    Notes,
    SelectedAccounts,
)
from ofmhelpers.features.accounts import page
from ofmhelpers.features.accounts.removal import remove_accounts
from ofmhelpers.features.accounts.tracker import sync_now
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, OwnerId, Scope, Storage
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/accounts", tags=["accounts"], dependencies=[Access(Area.ACCOUNTS)]
)

ListQuery = Annotated[AccountQuery, Query()]


class _Count(BaseModel):
    count: int


@router.get("")
def index(request: Request, query: ListQuery, scope: Scope) -> Response:
    view = page.list_view(query, scope)
    return render(request, "accounts.html", {"query": query, **dict(view)})


@router.post("/add")
def add(body: AddAccounts, owner_id: OwnerId) -> AddResult:
    return page.add(body, owner_id)


@router.post("/sync")
def sync(owner_id: OwnerId) -> _Count:
    return _Count(count=sync_now(owner_id))


@router.post("/creator")
def creator(body: AssignCreator, scope: Scope) -> _Count:
    return _Count(count=page.assign_creator(body.ids, body.creator, scope))


@router.post("/delete")
def delete(body: SelectedAccounts, scope: Scope, storage: Storage) -> _Count:
    return _Count(count=remove_accounts(body.ids, scope, storage.folders))


@router.post("/{account_id}/notes")
def notes(account_id: int, body: Notes, scope: Scope) -> Notes:
    page.set_notes(account_id, body.notes, scope)
    return body


@router.get("/{account_id}")
def analytics(
    request: Request, account_id: int, query: ListQuery, scope: Scope
) -> Response:
    view = page.analytics_view(account_id, query, scope)
    return render(request, "account_analytics.html", {"query": query, **dict(view)})
