from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.db.short_links.schema import SelfDomainForm, SelfRange
from ofmhelpers.features.links import self_page
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/self-links", tags=["self-links"], dependencies=[Access(Area.SELF_LINKS)]
)


@router.get("")
def index(
    request: Request, query: Annotated[SelfRange, Query()], scope: Scope
) -> Response:
    view = self_page.page_view(query, scope)
    return render(request, "self_links.html", query=query, **dict(view))


@router.post("/domains")
def add_domain(form: Annotated[SelfDomainForm, Form()], owner_id: OwnerId) -> Response:
    self_page.add_domain(form.host, owner_id)
    return RedirectResponse("/self-links", status_code=303)


@router.post("/domains/{domain_id}/status")
def set_status(
    domain_id: int, owner_id: OwnerId, status: Annotated[str, Form()]
) -> Response:
    self_page.set_status(domain_id, status, owner_id)
    return RedirectResponse("/self-links", status_code=303)


@router.post("/domains/{domain_id}/delete")
def delete_domain(domain_id: int, owner_id: OwnerId) -> Response:
    self_page.delete_domain(domain_id, owner_id)
    return RedirectResponse("/self-links", status_code=303)
