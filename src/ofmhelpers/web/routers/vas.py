from typing import Annotated

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import Response

from ofmhelpers.auth.access import Area
from ofmhelpers.features.vas import service
from ofmhelpers.web.dependencies import Access, Viewing
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/vas", tags=["vas"], dependencies=[Access(Area.VAS)])


@router.get("", summary="What each VA did and spent")
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
    return render(request, "vas.html", **dict(view))
