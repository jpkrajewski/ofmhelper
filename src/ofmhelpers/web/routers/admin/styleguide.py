from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/styleguide",
    tags=["styleguide"],
    dependencies=[Access(Area.STYLEGUIDE)],
)


@router.get("")
def styleguide(request: Request) -> Response:
    return render(request, "styleguide.html")
