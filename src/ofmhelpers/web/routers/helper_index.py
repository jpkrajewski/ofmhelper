"""
ofmhelpers/web/routers/helpers_index.py
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.helpers_registry import HELPERS

router = APIRouter(prefix="/helpers", tags=["helpers"])


@router.get("", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "helpers_index.html", {"helpers": HELPERS}
    )
