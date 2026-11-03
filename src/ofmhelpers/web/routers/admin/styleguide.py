"""
ofmhelpers/web/routers/admin/styleguide.py

/styleguide: every element of components/ui.html, rendered once with each
variant, so a change to a role token or a macro is checked on one page
before it lands on every other. Admin only; no data.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.web.dependencies import AdminOnly
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/styleguide",
    tags=["styleguide"],
    dependencies=[AdminOnly],
)


@router.get("")
def styleguide(request: Request) -> Response:
    return render(request, "styleguide.html")
