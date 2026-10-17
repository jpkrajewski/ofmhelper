"""
ofmhelpers/web/routers/downloads/index.py

Unified page for Download videos / Download images / Clean images, in the
same style as /generate: one form with a tool picker on the left, and a
non-blocking gallery of the last 20 runs across all three tools on the
right. Submissions POST to each tool's existing /run endpoint.
"""

from fastapi import APIRouter, Request

from ofmhelpers.config import settings
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, task_card
from ofmhelpers.web.stores.jobs import list_jobs_page
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/download-assets", tags=["download-assets"])

GALLERY_LIMIT = settings.web.gallery_limit


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page(
        set(DOWNLOAD_TOOLS), 0, GALLERY_LIMIT, AuthMiddleware.visible_actor(request)
    )
    return render(
        request, "download_assets_form.html", gallery=[task_card(j) for j in recent]
    )
