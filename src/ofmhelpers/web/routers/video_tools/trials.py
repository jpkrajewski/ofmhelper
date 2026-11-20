from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, Response

from ofmhelpers.auth.access import Area
from ofmhelpers.cache import JobQueue, QueueName, enqueue
from ofmhelpers.features.trials import folder
from ofmhelpers.web.dependencies import Access, CurrentUser, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/trials", tags=["trials"], dependencies=[Access(Area.TRIALS)]
)


def _encodes() -> Callable[..., object]:
    return JobQueue(name=QueueName.MEDIA).put


def _trials(login_id: int, user: CurrentUser, scope: Scope) -> folder.Trials:
    trials = folder.Trials(
        uid=user.id, scope=scope, login_id=login_id, encode=_encodes(), enqueue=enqueue
    )
    trials.owned()
    return trials


Trials = Annotated[folder.Trials, Depends(_trials)]


def _reel(key: str, trials: Trials) -> folder.Reel:
    return folder.Reel(trials=trials, key=key)


Reel = Annotated[folder.Reel, Depends(_reel)]


@router.get("")
def trials_page(request: Request, user: CurrentUser, scope: Scope) -> Response:
    cards = folder.phone_cards(user.id, scope)
    return render(request, "trials.html", board=cards.board, progress=cards.progress)


@router.get("/{login_id}")
def folder_page(request: Request, trials: Trials) -> Response:
    opened = trials.open()
    return render(request, "trials_folder.html", card=opened.card, view=opened.view)


@router.get("/{login_id}/live")
def live(trials: Trials) -> folder.FolderLive:
    return trials.live()


@router.get("/{login_id}/cards")
def cards(
    request: Request,
    trials: Trials,
    key: Annotated[list[str] | None, Query()] = None,
) -> Response:
    view = trials.view()
    wanted = set(key or [])
    return render(
        request,
        "_trials_cards.html",
        view=view,
        reels=view.made_among(wanted),
        pending=view.pending_among(wanted),
        failed=view.failed_among(wanted),
    )


@router.post("/{login_id}/generate")
def generate(trials: Trials) -> folder.Started:
    return trials.generate()


@router.post("/{login_id}/reel/{key}/regenerate")
def regenerate(reel: Reel) -> folder.Started:
    return reel.remake()


@router.post("/{login_id}/reel/{key}/caption")
def set_caption(caption: Annotated[str, Form()], reel: Reel) -> folder.Started:
    return reel.apply_caption(caption)


@router.post("/{login_id}/reel/{key}/captions")
def write_captions(reel: Reel) -> folder.Started:
    return reel.ask_captions()


@router.post("/{login_id}/reel/{key}/select")
def select(reel: Reel, selected: Annotated[bool, Form()] = False) -> folder.Selected:
    return reel.select(selected)


@router.post("/{login_id}/upload")
def upload(
    trials: Trials, approved: Annotated[list[str] | None, Form()] = None
) -> folder.Uploading:
    return trials.upload(approved)


@router.get("/files/{login_id}/{key}")
def video(reel: Reel) -> Response:
    return FileResponse(reel.playable(), media_type="video/mp4")


@router.get("/files/{login_id}/{key}/poster")
def poster(reel: Reel) -> Response:
    return FileResponse(reel.poster(), media_type="image/webp")
