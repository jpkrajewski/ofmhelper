# Copyright (C) 2026 jpkrajewski

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.features.vas import service
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Viewing, Zone
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/vas", tags=["vas"], dependencies=[Access(Area.VAS)])


@router.get("", summary="What each VA did and spent")
def page(
    request: Request,
    viewer: Viewing,
    zone: Zone,
) -> Response:
    view = service.page_view(
        viewer.user.id,
        viewer.user_ids,
        everyone=viewer.user.is_admin,
        zone=zone,
    )
    return render(request, "vas.html", dict(view))
