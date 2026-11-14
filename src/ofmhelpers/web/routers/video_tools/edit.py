"""/edit: cut one part of a video, cropped and sped up or down, or save one
frame. /clone's "Trim it!" (a video over Wan 3.0's 15 s) opens it as
`?video=<stored path>`; without one the page asks for a video (upload or
reuse) and opens it. The page plays the video (a light copy once the edit
worker made it) over a strip of its frames with a marker and two handles;
Cut queues the part on the edit worker and the page polls it: Clone it
(/clone with it picked) / Download; Frame downloads the frame shown.
Nothing it makes is kept past a day. The work behind each route:
`features.edit.service`, `run_cut` and `run_prepare` included: jobs queued
before 2026-11-11 name them by this module's path, so they stay importable here."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field

from ofmhelpers.auth.access import Area
from ofmhelpers.config import settings
from ofmhelpers.features.clone.targets import clone_targets
from ofmhelpers.features.edit import service
from ofmhelpers.features.edit.options import SPEEDS, Aspect, Framing, Size, Speed
from ofmhelpers.features.edit.service import (
    PREFIX,
    CutState,
    EditVideo,
    Prepared,
    run_cut,
    run_prepare,
)
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix=PREFIX, tags=["edit"], dependencies=[Access(Area.EDIT)])

_PREPARED_CACHE = {"Cache-Control": "private, max-age=86400"}


class CutRequest(Framing):
    video: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    speed: Speed = 1.0


class FrameQuery(Framing):
    video: str
    t: float = Field(ge=0, allow_inf_nan=False)


def _limit_s() -> int:
    """The longest video /clone takes (every Wan 3.0 provider: 15 s): what
    the page picks first and marks when the part is longer."""
    return min(t.max_reference_s for t in clone_targets().all())


@router.get("")
def page(
    request: Request,
    assets_root: AssetsRoot,
    actor: Actor,
    video: Annotated[str | None, Query()] = None,
) -> Response:
    return render(
        request,
        "edit.html",
        page=service.page(video, assets_root, actor, run_prepare),
        limit_s=_limit_s(),
        min_s=settings.web.edit_min_s,
        max_part_s=settings.web.edit_max_part_s,
        aspects=list(Aspect),
        speeds=SPEEDS,
        sizes=list(Size),
        default_size=Framing().size,
    )


@router.post("/upload", status_code=201)
def upload(
    assets_root: AssetsRoot, actor: Actor, file: Annotated[UploadFile, File()]
) -> EditVideo:
    """A video from the person's computer, to open here."""
    return service.upload(file, assets_root, actor, run_prepare)


@router.get("/prepared")
def prepared(
    assets_root: AssetsRoot, actor: Actor, video: Annotated[str, Query()]
) -> Prepared:
    return service.prepared_state(video, assets_root, actor)


@router.get("/proxy")
def proxy(assets_root: AssetsRoot, video: Annotated[str, Query()]) -> Response:
    path = service.prepared_file(video, assets_root, "proxy")
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="video/mp4", headers=_PREPARED_CACHE)


@router.get("/frames")
def frames(assets_root: AssetsRoot, video: Annotated[str, Query()]) -> Response:
    path = service.prepared_file(video, assets_root, "frames")
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="application/json", headers=_PREPARED_CACHE)


@router.get("/strip")
def strip_image(assets_root: AssetsRoot, video: Annotated[str, Query()]) -> Response:
    path = service.prepared_file(video, assets_root, "strip")
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers=_PREPARED_CACHE)


@router.get("/frame")
def frame(assets_root: AssetsRoot, query: Annotated[FrameQuery, Query()]) -> Response:
    grab = service.frame(query.video, query.t, query, assets_root)
    return Response(
        grab.data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(grab.name)}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/cut", status_code=202)
def cut(assets_root: AssetsRoot, actor: Actor, body: CutRequest) -> JobQueued:
    return JobQueued(
        job_id=service.queue_cut(
            body.video,
            body.start,
            body.end,
            body,
            body.speed,
            assets_root,
            actor,
            run_cut,
        )
    )


@router.get("/cut/{job_id}")
def cut_state(job_id: str, actor: Actor) -> CutState:
    return service.cut_state(job_id, actor)
