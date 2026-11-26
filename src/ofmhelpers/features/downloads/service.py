# Copyright (C) 2026 jpkrajewski

from typing import Any
lazy from collections.abc import Callable
lazy from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.features.downloads import images as image_tool
from ofmhelpers.features.downloads import media as media_tool
from ofmhelpers.features.downloads import videos as video_tool
from ofmhelpers.features.downloads.again import Again
from ofmhelpers.features.downloads.links import LinkDownloads
from ofmhelpers.features.downloads.sheet import Clicker, SheetDownloads
from ofmhelpers.features.storage.service import StorageService


class DownloadService(BaseModel, frozen=True):
    storage: StorageService
    again: Again
    media: LinkDownloads
    videos: LinkDownloads
    images: LinkDownloads
    sheet: SheetDownloads

    @classmethod
    def compose(
        cls,
        actor: str,
        owner_id: int,
        assets_root: Path,
        storage: StorageService,
    ) -> DownloadService:
        again = Again(actor=actor)

        def tool(task: str, run: Callable[..., Any]) -> LinkDownloads:
            return LinkDownloads(
                task=task,
                run=run,
                actor=actor,
                owner_id=owner_id,
                assets_root=assets_root,
                again=again,
            )

        media = tool(media_tool.TASK, media_tool.run_downloads)
        return cls(
            storage=storage,
            again=again,
            media=media,
            videos=tool(video_tool.TASK, video_tool.run_downloads),
            images=tool(image_tool.TASK, image_tool.run_downloads),
            sheet=SheetDownloads(
                clicker=Clicker(user_id=owner_id),
                sheets=storage.sheet,
                links=media,
            ),
        )
