from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ofmhelpers.features.legal import page
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["legal"])


@router.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> Response:
    return render(request, "privacy.html", dict(page.page_view()))


@router.get("/terms", response_class=HTMLResponse)
def terms(request: Request) -> Response:
    return render(request, "terms.html", dict(page.page_view()))
