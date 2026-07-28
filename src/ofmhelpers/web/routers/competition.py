"""
ofmhelpers/web/routers/competition.py

One row per model, one column of competing Instagram profiles: the list the
team opens and scrolls every day. Add (one URL per line) / delete only --
anything richer belongs on the model's edit page. Shares the roster store
(web/models.py) and the Models pages' look (_models_style.html), and is
admin-gated the same way the roster is.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ofmhelpers.web import models as models_store
from ofmhelpers.web.auth import require_admin
from ofmhelpers.web.templates_config import templates

router = APIRouter(
    prefix="/competition", tags=["competition"], dependencies=[Depends(require_admin)]
)


@router.get("")
def list_page(request: Request):
    return templates.TemplateResponse(
        request, "competition.html", {"models": models_store.list_models()}
    )


@router.post("/{model_id}/add")
def add_competitors(model_id: str, urls: Annotated[str, Form()]):
    """One profile URL per line -- also covers the single-URL case."""
    lines = [u.strip() for u in urls.splitlines() if u.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="At least one URL is required")
    if models_store.add_competitors_bulk(model_id, lines) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return RedirectResponse(url="/competition", status_code=303)


@router.post("/{competitor_id}/delete")
def delete_competitor(competitor_id: str):
    if not models_store.delete_competitor(competitor_id):
        raise HTTPException(status_code=404, detail="Competitor not found")
    return RedirectResponse(url="/competition", status_code=303)
