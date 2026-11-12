"""/download-media: any links -- videos, photo posts, carousels -- in one go
(`features.downloads.media`). Replaced the separate Download videos / Download
images tools on /download-assets; their routes stay for their past jobs."""

from typing import Annotated

from fastapi import APIRouter, Form, Path
from pydantic import Field

from ofmhelpers.features.downloads import again, media, sheet

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.downloads.media import run_downloads as _run_downloads
from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.schemas import JobQueued
from ofmhelpers.schemas.access import Area
from ofmhelpers.schemas.downloads import DownloadsQueued, SheetQueued
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.forms import FormModel
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["TASK", "_run_downloads", "router"]

TASK = media.TASK

router = APIRouter(
    prefix="/download-media",
    tags=["download-media"],
    dependencies=[Access(Area.DOWNLOAD)],
)


class SendForm(FormModel):
    job_id: str
    target: str
    # The card's own file (`/files/{job}/{index}`).
    index: int | None = Field(default=None, ge=0)


@router.post("/run", status_code=202)
def run(
    urls: Annotated[str, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> DownloadsQueued:
    return media.start(urls, actor, owner_id, assets_root)


@router.post("/sheet-downloads", summary="Download today's links of the Drive sheet")
def sheet_downloads(
    actor: Actor, owner_id: OwnerId, assets_root: AssetsRoot
) -> SheetQueued:
    """Today's column of the sheet in the person's Drive root: every link
    not downloaded or queued before becomes one download job (`job_id`;
    none when nothing is new). 429 while the button rests."""
    return sheet.start(actor, owner_id, assets_root)


@router.post("/send")
def send(
    form: Annotated[SendForm, Form()], actor: Actor, assets_root: AssetsRoot
) -> Redirect:
    """Hand a finished download's video to /clone or /clone-long: answers the
    URL to open with it already picked."""
    return media.send(form.job_id, form.target, form.index, actor, assets_root)


@router.post("/{job_id}/replays", status_code=202, summary="Download it again")
def replay(job_id: str, actor: Actor) -> JobQueued:
    """A download whose video was removed, fetched again into the same job:
    answers the job the page polls. A second click while it runs queues
    nothing."""
    return again.replay(job_id, actor)


@router.get("/{job_id}/files/{index}/stored", summary="Its file in the asset store")
def stored_file(
    job_id: str, index: Annotated[int, Path(ge=0)], actor: Actor
) -> again.StoredFile:
    """Where a download's file is in the asker's part of the asset store:
    what a clone's Recreate picks once the video is back."""
    return again.stored_file(job_id, index, actor)


add_job_routes(
    router,
    "Download",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
