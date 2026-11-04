"""/trials: trial reels from a model's POSTED folder. The same phone cards
as /drive; Generate trials looks in her POSTED for new reels, downloads them and
runs the snapchat pipeline on each (a copy already made is left alone) with an
AI-written caption (watch reel -> transcribe/describe -> five viral captions).
The phone opens the folder, newest first: the copies as they are made, choose a
caption or write one, Regenerate (pipeline only), select the good ones, Upload
replaces what is in the TRIALS folder next to POSTED in her Drive folder with
them, after asking.

The folder page is driven by trials.js: every action is a fetch answered
at once (JSON), the work is a job, the page polls `/live` (one small JSON:
each card's stamp) and swaps in only the cards that changed (`/cards`).
Both roles; a user only ever sees their own phones' folders (the same scope
as /drive). The work behind each route: `features.trials.folder`."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response

from ofmhelpers.cache import JobQueue, QueueName, enqueue
from ofmhelpers.features.trials import folder
from ofmhelpers.web.dependencies import CurrentUser, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/trials", tags=["trials"])


def _encodes() -> Callable[..., object]:
    """What makes a copy waits its turn: 4K encodes, one at a time."""
    return JobQueue(QueueName.MEDIA).put


@router.get("")
def trials_page(request: Request, user: CurrentUser, scope: Scope) -> Response:
    """The phone cards. A card whose Generate runs keeps a way into its
    folder ("Making… 0 of 21"): with nothing made yet it showed no button,
    as if the click had been lost (prod 2026-11-02)."""
    board, progress = folder.phone_cards(user.id, scope)
    return render(request, "trials.html", board=board, progress=progress)


@router.get("/{login_id}")
def folder_page(
    request: Request, login_id: int, user: CurrentUser, scope: Scope
) -> Response:
    card, view = folder.open_folder(user.id, scope, login_id)
    return render(request, "trials_folder.html", card=card, view=view)


@router.get("/{login_id}/live")
def live(login_id: int, user: CurrentUser) -> folder.FolderLive:
    return folder.live(user.id, login_id)


@router.get("/{login_id}/cards")
def cards(
    request: Request,
    login_id: int,
    user: CurrentUser,
    scope: Scope,
    key: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """The cards `key` as the page renders them, for trials.js to swap in."""
    view = folder.view_of_own(user.id, scope, login_id)
    wanted = set(key or [])
    return render(
        request,
        "_trials_cards.html",
        view=view,
        reels=view.made_among(wanted),
        pending=view.pending_among(wanted),
    )


@router.post("/{login_id}/generate")
def generate(login_id: int, user: CurrentUser, scope: Scope) -> folder.Started:
    return folder.generate(user.id, scope, login_id, _encodes())


@router.post("/{login_id}/reel/{key}/regenerate")
def regenerate(login_id: int, key: str, user: CurrentUser) -> folder.Started:
    """The pipeline again on one reel, a new variant with its caption as it
    is: no AI call (its daily quota is small)."""
    return folder.remake(user.id, login_id, key, _encodes())


@router.post("/{login_id}/reel/{key}/caption")
def set_caption(
    login_id: int,
    key: str,
    caption: Annotated[str, Form()],
    user: CurrentUser,
) -> folder.Started:
    return folder.apply_caption(user.id, login_id, key, caption, _encodes())


@router.post("/{login_id}/reel/{key}/captions")
def write_captions(login_id: int, key: str, user: CurrentUser) -> folder.Started:
    """Captions to choose from, for a reel made before there were any."""
    return folder.ask_captions(user.id, login_id, key, enqueue)


@router.post("/{login_id}/reel/{key}/select")
def select(
    login_id: int,
    key: str,
    user: CurrentUser,
    selected: Annotated[bool, Form()] = False,
) -> folder.Selected:
    """Select / unselect, kept at once."""
    return folder.select(user.id, login_id, key, selected)


@router.post("/{login_id}/upload")
def upload(
    login_id: int,
    user: CurrentUser,
    approved: Annotated[list[str] | None, Form()] = None,
) -> folder.Uploading:
    return folder.upload(user.id, login_id, approved, enqueue)


@router.get("/files/{login_id}/{key}")
def video(login_id: int, key: str, user: CurrentUser) -> Response:
    return FileResponse(folder.playable(user.id, login_id, key), media_type="video/mp4")


@router.get("/files/{login_id}/{key}/poster")
def poster(login_id: int, key: str, user: CurrentUser) -> Response:
    return FileResponse(folder.poster(user.id, login_id, key), media_type="image/webp")
