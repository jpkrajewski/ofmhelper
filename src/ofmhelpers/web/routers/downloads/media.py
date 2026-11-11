"""/download-media: any links -- videos, photo posts, carousels -- in one go
(`features.downloads.media`). Replaced the separate Download videos / Download
images tools on /download-assets; their routes stay for their past jobs."""

from typing import Annotated

from fastapi import APIRouter, Form
from pydantic import Field

from ofmhelpers.features.downloads import media

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.downloads.media import run_downloads as _run_downloads
from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Actor, AssetsRoot, OwnerId
from ofmhelpers.web.forms import FormModel
from ofmhelpers.web.routers.task_helpers import add_job_routes

__all__ = ["TASK", "_run_downloads", "router"]

TASK = media.TASK

router = APIRouter(prefix="/download-media", tags=["download-media"])


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
) -> JobQueued:
    return media.start(urls, actor, owner_id, assets_root)


@router.post("/send")
def send(
    form: Annotated[SendForm, Form()], actor: Actor, assets_root: AssetsRoot
) -> Redirect:
    """Hand a finished download's video to /clone or /clone-long: answers the
    URL to open with it already picked."""
    return media.send(form.job_id, form.target, form.index, actor, assets_root)


add_job_routes(
    router,
    "Download",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
