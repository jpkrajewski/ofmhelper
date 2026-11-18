"""/threads: pictures for Threads from one POSTED reel. The same phone cards
as /trials; an account's page lists its POSTED reels, one click makes
pictures of one (stills written as the phone's own photos) into
THREADS/<reel> next to POSTED in its Drive folder. The page refreshes
itself while a reel is being made. The work: `features.threads`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from ofmhelpers.auth.access import Area
from ofmhelpers.features.threads import service
from ofmhelpers.web.dependencies import Access, CurrentUser, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/threads", tags=["threads"], dependencies=[Access(Area.THREADS)]
)


def _threads(login_id: int, user: CurrentUser, scope: Scope) -> service.Threads:
    return service.Threads(uid=user.id, scope=scope, login_id=login_id)


# The signed-in person's /threads of the login in the path.
Threads = Annotated[service.Threads, Depends(_threads)]


@router.get("")
def threads_page(request: Request, user: CurrentUser, scope: Scope) -> Response:
    return render(request, "threads.html", board=service.board(user.id, scope))


@router.get("/{login_id}")
def folder_page(request: Request, threads: Threads) -> Response:
    return render(request, "threads_folder.html", folder=threads.open())


@router.post("/{login_id}/reels/{key}/pictures")
def make_pictures(threads: Threads, key: str) -> RedirectResponse:
    threads.start(key)
    return RedirectResponse(
        f"/threads/{threads.login_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{login_id}/reels/{key}/pictures/{name}")
def picture(threads: Threads, key: str, name: str) -> FileResponse:
    return FileResponse(threads.picture(key, name), media_type="image/jpeg")
