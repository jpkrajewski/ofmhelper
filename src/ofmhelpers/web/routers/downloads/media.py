from typing import Annotated

from fastapi import APIRouter, Form, Path
from pydantic import Field

from ofmhelpers.db.downloads.schema import DownloadsQueued, SheetQueued
from ofmhelpers.features.assets import send as sending
from ofmhelpers.features.downloads import again, media
from ofmhelpers.features.downloads.media import run_downloads as _run_downloads
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.features.redirect import Redirect
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actors, AssetsRoot, Downloads
from ofmhelpers.web.forms import FormModel
from ofmhelpers.web.idempotency import Idempotent, IdempotentRoute
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["TASK", "_run_downloads", "router"]

TASK = media.TASK

router = APIRouter(
    prefix="/download-media",
    tags=["download-media"],
    dependencies=[Access(Area.DOWNLOAD)],
    route_class=IdempotentRoute,
)


class SendForm(FormModel):
    job_id: str
    target: str
    index: int | None = Field(default=None, ge=0)


@router.post("/run", status_code=202, dependencies=[Idempotent])
def run(urls: Annotated[str, Form()], downloads: Downloads) -> DownloadsQueued:
    return downloads.media.start(urls)


@router.post("/sheet-downloads", summary="Download today's links of the Drive sheet")
def sheet_downloads(downloads: Downloads) -> SheetQueued:
    return downloads.sheet.start()


@router.post("/send")
def send(
    form: Annotated[SendForm, Form()], actors: Actors, assets_root: AssetsRoot
) -> Redirect:
    return sending.send(form.job_id, form.target, form.index, actors, assets_root)


@router.post("/{job_id}/replays", status_code=202, summary="Download it again")
def replay(job_id: str, downloads: Downloads) -> JobQueued:
    return downloads.again.replay(job_id)


@router.get("/{job_id}/files/{index}/stored", summary="Its file in the asset store")
def stored_file(
    job_id: str, index: Annotated[int, Path(ge=0)], downloads: Downloads
) -> again.StoredFile:
    return downloads.again.stored_file(job_id, index)


add_job_routes(
    router,
    "Download",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
