# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status

from ofmhelpers.features.storage.models import BinPart
from ofmhelpers.features.storage.schema import MoveStarted
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Storage
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/storage",
    tags=["storage"],
    dependencies=[Access(Area.STORAGE)],
)

FolderId = Annotated[str | None, Form(pattern=r"^[A-Za-z0-9_-]{10,128}$")]


@router.get("", summary="Storage used per account folder")
def storage_page(request: Request, storage: Storage) -> Response:
    return render(request, "storage.html", {"view": storage.bin.page()})


@router.post(
    "/bin-moves",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Move a folder part to the provider's bin",
)
def move_to_bin(
    storage: Storage,
    part: Annotated[BinPart, Form()],
    folder_id: FolderId = None,
) -> MoveStarted:
    return storage.bin.start_move(part, folder_id)
