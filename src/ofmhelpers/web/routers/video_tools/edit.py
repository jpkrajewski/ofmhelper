"""/edit: cut one part of a video. /clone's "Trim it!" (a video over Wan
3.0's 15 s) opens it as `?video=<stored path>`; without one the page asks
for a video (upload or reuse) and opens it. The page plays the video over a
strip of its frames with two handles; Cut queues the part on the edit
worker and the page polls it: Clone it (/clone with it picked) / Download.
Nothing it makes is kept past a day. The work behind each route:
`features.edit.service`; `run_cut` stays here: RQ stores a job's function
by its path, and a worker imports the routers first."""

from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.features.clone.targets import clone_targets
from ofmhelpers.features.edit import service
from ofmhelpers.features.edit.service import PREFIX, CutState, EditVideo
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Actor, AssetsRoot
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix=PREFIX, tags=["edit"])


class CutRequest(BaseModel):
    video: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


def _limit_s() -> int:
    """The longest video /clone takes (every Wan 3.0 provider: 15 s): what
    the page picks first and marks when the part is longer."""
    return min(t.max_reference_s for t in clone_targets().values())


def run_cut(**kwargs: Any) -> dict[str, Any]:
    """The edit worker's entry point (`service.run_cut`)."""
    kwargs.pop("job_id", None)
    return service.run_cut(**kwargs)


@router.get("")
def page(
    request: Request,
    assets_root: AssetsRoot,
    video: Annotated[str | None, Query()] = None,
) -> Response:
    return render(
        request,
        "edit.html",
        page=service.page(video, assets_root),
        limit_s=_limit_s(),
        min_s=settings.web.edit_min_s,
        max_part_s=settings.web.edit_max_part_s,
    )


@router.post("/upload")
def upload(assets_root: AssetsRoot, file: Annotated[UploadFile, File()]) -> EditVideo:
    """A video from the person's computer, to open here."""
    return service.upload(file, assets_root)


@router.get("/strip")
def strip_image(assets_root: AssetsRoot, video: Annotated[str, Query()]) -> Response:
    return FileResponse(
        service.strip(video, assets_root),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/cut")
def cut(assets_root: AssetsRoot, actor: Actor, body: CutRequest) -> JobQueued:
    return JobQueued(
        job_id=service.queue_cut(
            body.video, body.start, body.end, assets_root, actor, run_cut
        )
    )


@router.get("/cut/{job_id}")
def cut_state(job_id: str, actor: Actor) -> CutState:
    return service.cut_state(job_id, actor)
