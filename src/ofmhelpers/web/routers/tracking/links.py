"""
/links: short links on link providers (Oopsie, Bouncy; `ofmhelpers.integrations.short_links`)
with their views, clicks and CTR over one period
(`settings.links.periods_days`, picked on the page), the Instagram account
each bridges from, its creator (model) and target page; KPI totals over
the filtered list and each provider's quota on top. Filters, period, sort
and paging are the query string (`schemas.LinkQuery`), so every view is a
link.

"Generate links" makes one 18+ gated link per picked Instagram account of
a model, all to one target page. Making, syncing, the 18+ fix and deleting
run on the worker (features/links/sync.py); static/js/links.js reloads while a
row is still being made or deleted.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.features.links import page
from ofmhelpers.schemas import (
    AssignLinkCreator,
    GenerateLinks,
    LinkQuery,
    LinksQueued,
    SelectedLinks,
)
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access, OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/links", tags=["links"], dependencies=[Access(Area.LINKS)])


class _Count(BaseModel):
    count: int


@router.get("")
def index(
    request: Request, query: Annotated[LinkQuery, Query()], scope: Scope
) -> Response:
    view = page.list_view(query, scope)
    return render(request, "links.html", query=query, **dict(view))


@router.post("/generate", status_code=202)
def generate(body: GenerateLinks, owner_id: OwnerId) -> LinksQueued:
    """One link per picked account, queued; the rows show while being
    made."""
    return page.generate(body, owner_id)


@router.post("/sync")
def sync(scope: Scope) -> LinksQueued:
    """The signed-in person's links synced now, on every provider they have
    a key for."""
    return page.sync_now(scope)


@router.post("/creator")
def creator(body: AssignLinkCreator, scope: Scope) -> _Count:
    """Assigns the links to one of the person's creators ("" = none)."""
    return _Count(count=page.assign_creator(body.ids, body.creator, scope))


@router.post("/adult")
def adult(body: SelectedLinks, scope: Scope) -> LinksQueued:
    """Turns the 18+ confirmation on for the links."""
    return page.gate(body.ids, scope)


@router.post("/delete")
def delete(body: SelectedLinks, scope: Scope) -> LinksQueued:
    """Deletes the links on their provider (their address stops working),
    then here; a failed one (never made) is only removed here."""
    return page.delete(body.ids, scope)
