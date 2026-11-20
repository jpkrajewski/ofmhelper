from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ofmhelpers.features.server.status import duration, server_status
from ofmhelpers.web.access import Action, Area
from ofmhelpers.web.dependencies import Access, Can
from ofmhelpers.web.templates_config import render

router = APIRouter(tags=["home"])


class Health(BaseModel):
    status: str = "ok"


@router.get("/home", response_class=HTMLResponse, dependencies=[Access(Area.HOME)])
def home(request: Request) -> Response:
    return render(request, "home.html")


@router.get(
    "/home/server",
    dependencies=[Access(Area.HOME), Can(Action.SERVER_STATUS)],
)
def server(request: Request) -> Response:
    return render(
        request, "_server_status.html", server=server_status(), duration=duration
    )


@router.get("/health")
def health() -> Health:
    return Health()
