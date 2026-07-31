"""
ofmhelpers/web/routers/helpers_index.py
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ofmhelpers.web.routers.helpers.registry import HELPERS
from ofmhelpers.web.templates_config import get_templates

router = APIRouter(prefix="/helpers", tags=["helpers"])


@router.get("", response_class=HTMLResponse)
def index(request: Request):
    return get_templates().TemplateResponse(
        request, "helpers_index.html", {"helpers": HELPERS}
    )
