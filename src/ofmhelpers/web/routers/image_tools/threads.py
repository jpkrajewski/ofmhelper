# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from ofmhelpers.features.threads import service
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, CurrentUser, Storage
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Access(Area.THREADS)],
)


def _threads(login_id: int, user: CurrentUser, storage: Storage) -> service.Threads:
    return service.Threads(uid=user.id, folders=storage.folders, login_id=login_id)


Threads = Annotated[service.Threads, Depends(_threads)]


def _index_cards(user: CurrentUser, storage: Storage) -> service.PhoneCards:
    return service.Threads.phone_cards(user.id, storage.folders)


IndexCards = Annotated[service.PhoneCards, Depends(_index_cards)]


@router.get("")
def threads_page(request: Request, cards: IndexCards) -> Response:
    return render(
        request,
        "threads.html",
        {"board": cards.board, "progress": cards.progress},
    )


@router.get("/{login_id}")
def folder_page(request: Request, threads: Threads) -> Response:
    return render(request, "threads_folder.html", {"folder": threads.open()})


@router.post("/{login_id}/reels/{key}/pictures")
def make_pictures(threads: Threads, key: str) -> RedirectResponse:
    threads.start(key)
    return RedirectResponse(
        f"/threads/{threads.login_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{login_id}/reels/{key}/pictures/{name}")
def picture(threads: Threads, key: str, name: str) -> FileResponse:
    return FileResponse(threads.picture(key, name), media_type="image/jpeg")
