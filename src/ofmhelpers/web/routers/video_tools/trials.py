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

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, Response

from ofmhelpers.cache import JobQueue, QueueName, enqueue
from ofmhelpers.features.trials import folder
from ofmhelpers.web.dependencies import CurrentUser, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/trials", tags=["trials"])


def _encodes() -> Callable[..., object]:
    """What makes a copy waits its turn: 4K encodes, one at a time."""
    return JobQueue(QueueName.MEDIA).put


def _trials(login_id: int, user: CurrentUser, scope: Scope) -> folder.Trials:
    return folder.Trials(
        uid=user.id, scope=scope, login_id=login_id, encode=_encodes(), enqueue=enqueue
    )


# The signed-in person's trials of the login in the path.
Trials = Annotated[folder.Trials, Depends(_trials)]


@router.get("")
def trials_page(request: Request, user: CurrentUser, scope: Scope) -> Response:
    board, progress = folder.phone_cards(user.id, scope)
    return render(request, "trials.html", board=board, progress=progress)


@router.get("/{login_id}")
def folder_page(request: Request, trials: Trials) -> Response:
    card, view = trials.open()
    return render(request, "trials_folder.html", card=card, view=view)


@router.get("/{login_id}/live")
def live(trials: Trials) -> folder.FolderLive:
    return trials.live()


@router.get("/{login_id}/cards")
def cards(
    request: Request,
    trials: Trials,
    key: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """The cards `key` as the page renders them, for trials.js to swap in."""
    view = trials.view()
    wanted = set(key or [])
    return render(
        request,
        "_trials_cards.html",
        view=view,
        reels=view.made_among(wanted),
        pending=view.pending_among(wanted),
    )


@router.post("/{login_id}/generate")
def generate(trials: Trials) -> folder.Started:
    return trials.generate()


@router.post("/{login_id}/reel/{key}/regenerate")
def regenerate(key: str, trials: Trials) -> folder.Started:
    """The pipeline again on one reel, a new variant with its caption as it
    is: no AI call (its daily quota is small)."""
    return trials.remake(key)


@router.post("/{login_id}/reel/{key}/caption")
def set_caption(
    key: str, caption: Annotated[str, Form()], trials: Trials
) -> folder.Started:
    return trials.apply_caption(key, caption)


@router.post("/{login_id}/reel/{key}/captions")
def write_captions(key: str, trials: Trials) -> folder.Started:
    """Captions to choose from, for a reel made before there were any."""
    return trials.ask_captions(key)


@router.post("/{login_id}/reel/{key}/select")
def select(
    key: str, trials: Trials, selected: Annotated[bool, Form()] = False
) -> folder.Selected:
    """Select / unselect, kept at once."""
    return trials.select(key, selected)


@router.post("/{login_id}/upload")
def upload(
    trials: Trials, approved: Annotated[list[str] | None, Form()] = None
) -> folder.Uploading:
    return trials.upload(approved)


@router.get("/files/{login_id}/{key}")
def video(key: str, trials: Trials) -> Response:
    return FileResponse(trials.playable(key), media_type="video/mp4")


@router.get("/files/{login_id}/{key}/poster")
def poster(key: str, trials: Trials) -> Response:
    return FileResponse(trials.poster(key), media_type="image/webp")
