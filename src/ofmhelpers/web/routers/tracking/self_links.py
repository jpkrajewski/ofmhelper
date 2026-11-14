"""
/self-links: our own bridge pages (provider "self"). It owns two things the
rented providers can't give us -- the custom domains we serve the pages on
(add, enable/disable, remove), and every click on them over any time frame,
with the timestamps, countries, devices and referrers the public bridge logs
(web/middleware/bridge.py). The links themselves sit on /links beside Oopsie
and Bouncy; this page is their domains and their numbers
(`features/links/self_page.py`).
"""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.auth.access import Area
from ofmhelpers.features.links import self_page
from ofmhelpers.schemas.self_links import SelfDomainForm, SelfRange
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
    """Starts serving bridge pages on `host` for this owner."""
    self_page.add_domain(form.host, owner_id)
    return RedirectResponse("/self-links", status_code=303)


@router.post("/domains/{domain_id}/status")
def set_status(
    domain_id: int, owner_id: OwnerId, status: Annotated[str, Form()]
) -> Response:
    """Turns serving on (active) or off (disabled) for the domain."""
    self_page.set_status(domain_id, status, owner_id)
    return RedirectResponse("/self-links", status_code=303)


@router.post("/domains/{domain_id}/delete")
def delete_domain(domain_id: int, owner_id: OwnerId) -> Response:
    """Removes the domain (its links stop serving)."""
    self_page.delete_domain(domain_id, owner_id)
    return RedirectResponse("/self-links", status_code=303)
