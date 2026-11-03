"""/trials: trial reels from a model's POSTED folder. The same phone cards
as /drive; Generate trials downloads her POSTED reels and runs the snapchat
pipeline on each with an AI-written caption (watch reel -> transcribe/
describe -> one viral caption). The phone opens the folder: the copies as
they are made, fix a caption, Regenerate (pipeline only), select the good
ones, Upload files them into a TRIALS folder next to POSTED in her Drive
folder.

The folder page is driven by trials.js: every action is a fetch answered
at once (JSON), the work is a job, the page polls `/live` (one small JSON:
each card's stamp) and swaps in only the cards that changed (`/cards`).
Both roles; a user only ever sees their own phones' folders (the same scope
as /drive). The work behind each route: `features.trials.folder`."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ofmhelpers.cache import enqueue
from ofmhelpers.features.trials import folder
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.templates_config import render


def _not_found_as_404() -> Iterator[None]:
    try:
        yield
    except folder.NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


router = APIRouter(
    prefix="/trials", tags=["trials"], dependencies=[Depends(_not_found_as_404)]
)


def _actor(request: Request) -> tuple[int, int | None]:
    """(user id, /drive scope) of the session."""
    user = AuthMiddleware.user(request)
    return user.id, AuthMiddleware.scope(request)


@router.get("")
def trials_page(request: Request):
    """The phone cards. A card whose Generate runs keeps a way into its
    folder ("Making… 0 of 21"): with nothing made yet it showed no button,
    as if the click had been lost (prod 2026-11-02)."""
    uid, scope = _actor(request)
    board, progress = folder.phone_cards(uid, scope)
    return render(request, "trials.html", board=board, progress=progress)


@router.get("/{login_id}")
def folder_page(request: Request, login_id: int):
    uid, scope = _actor(request)
    card, view = folder.open_folder(uid, scope, login_id)
    return render(request, "trials_folder.html", card=card, view=view)


@router.get("/{login_id}/live")
def live(request: Request, login_id: int) -> folder.FolderLive:
    uid, _scope = _actor(request)
    return folder.live(uid, login_id)


@router.get("/{login_id}/cards")
def cards(
    request: Request, login_id: int, key: Annotated[list[str] | None, Query()] = None
):
    """The cards `key` as the page renders them, for trials.js to swap in."""
    uid, scope = _actor(request)
    view = folder.view_of_own(uid, scope, login_id)
    wanted = set(key or [])
    return render(
        request,
        "_trials_cards.html",
        view=view,
        reels=view.made_among(wanted),
        pending=view.pending_among(wanted),
    )


@router.post("/{login_id}/generate")
def generate(request: Request, login_id: int) -> folder.Started:
    uid, scope = _actor(request)
    return folder.generate(uid, scope, login_id, enqueue)


@router.post("/{login_id}/reel/{key}/regenerate")
def regenerate(request: Request, login_id: int, key: str) -> folder.Started:
    """The pipeline again on one reel, a new variant with its caption as it
    is: no AI call (its daily quota is small)."""
    uid, _ = _actor(request)
    return folder.remake(uid, login_id, key, enqueue)


@router.post("/{login_id}/reel/{key}/caption")
def set_caption(
    request: Request,
    login_id: int,
    key: str,
    caption: Annotated[str, Form()],
) -> folder.Started:
    uid, _ = _actor(request)
    return folder.apply_caption(uid, login_id, key, caption, enqueue)


@router.post("/{login_id}/reel/{key}/select")
def select(
    request: Request,
    login_id: int,
    key: str,
    selected: Annotated[bool, Form()] = False,
) -> folder.Selected:
    """Select / unselect, kept at once."""
    uid, _ = _actor(request)
    return folder.select(uid, login_id, key, selected)


@router.post("/{login_id}/upload")
def upload(
    request: Request,
    login_id: int,
    approved: Annotated[list[str] | None, Form()] = None,
) -> folder.Uploading:
    uid, _ = _actor(request)
    return folder.upload(uid, login_id, approved, enqueue)


@router.get("/files/{login_id}/{key}")
def video(request: Request, login_id: int, key: str):
    uid, _ = _actor(request)
    return FileResponse(folder.playable(uid, login_id, key), media_type="video/mp4")


@router.get("/files/{login_id}/{key}/poster")
def poster(request: Request, login_id: int, key: str):
    uid, _ = _actor(request)
    return FileResponse(folder.poster(uid, login_id, key), media_type="image/webp")
