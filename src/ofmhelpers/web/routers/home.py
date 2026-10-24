"""/home (where a signed-in person lands: a card per page they may open,
from `schemas.nav.NAV`) and /health (the Docker and uptime probe, public)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["home"])


class Health(BaseModel):
    status: str = "ok"


@router.get("/home", response_class=HTMLResponse)
def home(request: Request):
    return render(request, "home.html")


@router.get("/health")
def health() -> Health:
    return Health()
