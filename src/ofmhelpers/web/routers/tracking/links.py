from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.auth.access import Area
from ofmhelpers.features.links import page
from ofmhelpers.schemas import (
    AssignLinkCreator,
    GenerateLinks,
    LinkQuery,
    LinksQueued,
    SelectedLinks,
)
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
    return page.generate(body, owner_id)


@router.post("/sync")
def sync(scope: Scope) -> LinksQueued:
    return page.sync_now(scope)


@router.post("/creator")
def creator(body: AssignLinkCreator, scope: Scope) -> _Count:
    return _Count(count=page.assign_creator(body.ids, body.creator, scope))


@router.post("/adult")
def adult(body: SelectedLinks, scope: Scope) -> LinksQueued:
    return page.gate(body.ids, scope)


@router.post("/delete")
def delete(body: SelectedLinks, scope: Scope) -> LinksQueued:
    return page.delete(body.ids, scope)
