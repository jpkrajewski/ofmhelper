"""
/spending: what the reader's VAs' runs cost today, in the last 7 days and
this month (`features/spending/service.py`). A user sees their own VAs, an
admin every VA under its owner; a VA cannot open it (`Area.SPENDING`). Days
are the header clock's time zone (cookie `ofm_timezone`).
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import Response

from ofmhelpers.features.spending import service
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access, Viewing
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/spending", tags=["spending"], dependencies=[Access(Area.SPENDING)]
)


@router.get("", summary="What each VA's runs cost")
def page(
    request: Request,
    viewer: Viewing,
    ofm_timezone: Annotated[str | None, Cookie()] = None,
) -> Response:
    view = service.page_view(
        viewer.user.id,
        viewer.user_ids,
        everyone=viewer.user.is_admin,
        zone=ofm_timezone,
    )
    return render(request, "spending.html", **dict(view))
