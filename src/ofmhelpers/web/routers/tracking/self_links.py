"""
/self-links: our own bridge pages (provider "self"). It owns two things the
rented providers can't give us -- the custom domains we serve the pages on
(add, enable/disable, remove), and every click on them over any time frame,
with the timestamps, countries, devices and referrers the public bridge logs
(web/middleware/bridge.py). The links themselves sit on /links beside Oopsie
and Bouncy; this page is their domains and their numbers.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.db.stores import self_links
from ofmhelpers.schemas.links import utc_today
from ofmhelpers.schemas.self_links import STAT_PERIODS, SelfDomainForm, SelfRange
from ofmhelpers.web.dependencies import OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/self-links", tags=["self-links"])


@router.get("")
def index(
    request: Request, query: Annotated[SelfRange, Query()], scope: Scope
) -> Response:
    return render(
        request,
        "self_links.html",
        stats=self_links.stats(scope, query, page=query.page),
        domains=self_links.domains(scope),
        pages=self_links.live_links(scope),
        page=query.page,
        query=query,
        periods=STAT_PERIODS,
        yesterday=utc_today() - timedelta(days=1),
    )


@router.post("/domains")
def add_domain(form: Annotated[SelfDomainForm, Form()], owner_id: OwnerId) -> Response:
    """Starts serving bridge pages on `host` for this owner."""
    if form.host in {d.host for d in self_links.domains(owner_id)}:
        raise HTTPException(status_code=400, detail=f"{form.host} is already added")
    self_links.add_domain(form.host, owner_id, status="active")
    return RedirectResponse("/self-links", status_code=303)


def _own_domain(domain_id: int, owner: int) -> None:
    if domain_id not in {d.id for d in self_links.domains(owner)}:
        raise HTTPException(status_code=404, detail="Domain not found")


@router.post("/domains/{domain_id}/status")
def set_status(
    domain_id: int, owner_id: OwnerId, status: Annotated[str, Form()]
) -> Response:
    """Turns serving on (active) or off (disabled) for the domain."""
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="Unknown status")
    _own_domain(domain_id, owner_id)
    self_links.set_domain_status(domain_id, status, owner_id)
    return RedirectResponse("/self-links", status_code=303)


@router.post("/domains/{domain_id}/delete")
def delete_domain(domain_id: int, owner_id: OwnerId) -> Response:
    """Removes the domain (its links stop serving)."""
    _own_domain(domain_id, owner_id)
    self_links.remove_domain(domain_id, owner_id)
    return RedirectResponse("/self-links", status_code=303)
