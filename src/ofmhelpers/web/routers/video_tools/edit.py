# Copyright (C) 2026 jpkrajewski

from typing import Annotated, Self
from urllib.parse import quote

from fastapi import APIRouter, File, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field, model_validator

from ofmhelpers.config import settings
from ofmhelpers.features.assets.models import VideoUpload
from ofmhelpers.features.clone_pricing.targets import clone_targets
from ofmhelpers.features.edit import service
from ofmhelpers.features.edit.clips import Clip
from ofmhelpers.features.edit.options import SPEEDS, Aspect, Framing, Size, Speed
from ofmhelpers.features.edit.service import (
    PREFIX,
    CutState,
    EditVideo,
    Prepared,
    run_cut,
    run_prepare,
)
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix=PREFIX, tags=["edit"], dependencies=[Access(Area.EDIT)])

_PREPARED_CACHE = {"Cache-Control": "private, max-age=86400"}


class CutRequest(Framing):
    video: str = ""
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    end: float = Field(default=0, ge=0, allow_inf_nan=False)
    clips: list[Clip] = Field(default_factory=list)
    speed: Speed = 1.0

    @model_validator(mode="after")
    def _one_clip_at_least(self) -> Self:
        if not self.clips and self.video:
            one = Clip(video=self.video, start=self.start, end=self.end)
            self.clips = [one]
        return self


class FrameQuery(Framing):
    video: str
    t: float = Field(ge=0, allow_inf_nan=False)


def _limit_s() -> int:
    return min(t.max_reference_s for t in clone_targets().values())


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
        {
            "page": service.Asked(
                video=video or "",
                assets_root=assets_root,
                actor=actor,
            ).page(run_prepare),
            "limit_s": _limit_s(),
            "min_s": settings.web.edit_min_s,
            "max_part_s": settings.web.edit_max_part_s,
            "max_clips": settings.web.edit_max_clips,
            "aspects": list(Aspect),
            "speeds": SPEEDS,
            "sizes": list(Size),
            "default_size": Framing().size,
        },
    )


@router.post("/upload", status_code=201)
def upload(
    assets_root: AssetsRoot,
    actor: Actor,
    file: Annotated[VideoUpload, File(media_type="video/*")],
) -> EditVideo:
    return service.upload(file, assets_root, actor, run_prepare)


@router.get("/video")
def video_to_add(
    assets_root: AssetsRoot,
    actor: Actor,
    video: Annotated[str, Query()],
) -> EditVideo:
    return service.Asked(video=video, assets_root=assets_root, actor=actor).add(
        run_prepare,
    )


@router.get("/prepared")
def prepared(
    assets_root: AssetsRoot,
    actor: Actor,
    video: Annotated[str, Query()],
) -> Prepared:
    return service.Asked(video=video, assets_root=assets_root, actor=actor).prepared()


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
            body.clips,
            body,
            body.speed,
            assets_root,
            actor,
            run_cut,
        ),
    )


@router.get("/cut/{job_id}")
def cut_state(job_id: str, actor: Actor) -> CutState:
    return service.cut_state(job_id, actor)
