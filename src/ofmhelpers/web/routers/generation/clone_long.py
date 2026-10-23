"""
/clone-long: one subject image + one video of any length (web/clone_long).
The page prices the pick live; `/run` measures the video itself, re-prices
it, checks every piece against the model and only then queues the job --
the stored price is the server's, never the browser's. Results are the
shared gallery card; the status page and files come from `add_job_routes`
(only the session's own jobs).
"""

import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.clone_long import (
    RESOLUTIONS,
    TARGETS,
    LongClone,
    Shape,
    VideoTools,
    rate,
    run_long_clone,
)
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    classify_kind,
    job_assets,
)
from ofmhelpers.web.schemas import Job, ProviderChoice, TaskCard
from ofmhelpers.web.stores.jobs import create_job, list_jobs_page, run_job
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/clone-long", tags=["clone-long"])

TASK = "clone_long"
LABEL = "Clone long"
FILES_PREFIX = f"{router.prefix}/files"
DEFAULT_RESOLUTION = "720P"


class LongCloneQueued(BaseModel):
    job_id: str


def card(job: Job) -> TaskCard:
    """A run as the shared gallery card, labelled with its price."""
    usd = job.params.get("usd")
    pieces = job.params.get("pieces")
    noun = "piece" if pieces == 1 else "pieces"
    label = f"Wan 3.0 · {pieces} {noun} · ${usd:.2f}" if usd is not None else LABEL
    return TaskCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        task_label=label,
        poll_prefix=router.prefix,
        result_kind="video",
    )


@router.get("")
def page(request: Request):
    jobs, _total = list_jobs_page(
        {TASK}, 0, settings.web.gallery_limit, AuthMiddleware.actor(request)
    )
    return render(
        request,
        "clone_long.html",
        gallery=[card(j) for j in jobs],
        providers=ProviderChoice.all(list(TARGETS)),
        selected_provider=settings.aigen.default_provider,
        api_keys=get_api_keys(request),
        rates={name: rate(name).model_dump() for name in TARGETS},
        max_piece_s=min(t.max_piece_s for t in TARGETS.values()),
        max_video_s=settings.clone_long.max_video_s,
        resolutions=RESOLUTIONS,
        default_resolution=DEFAULT_RESOLUTION,
        default_prompt=settings.web.clone_default_prompt,
    )


def _one(paths: list[str], kind: str, label: str) -> str:
    if len(paths) != 1 or classify_kind(paths[0]) != kind:
        raise HTTPException(status_code=400, detail=f"Pick one {label}")
    return paths[0]


def _measure(path: str) -> tuple[float, Shape]:
    """The video's length and shape as it plays; 400 when unreadable."""
    tools = VideoTools(settings.web.ffmpeg_timeout_s)
    try:
        return tools.length(Path(path)), tools.shape(Path(path))
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        raise HTTPException(
            status_code=400, detail="Could not read the video's length"
        ) from exc


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()] = "",
    provider: Annotated[str, Form()] = "",
    prompt: Annotated[str, Form(max_length=2000)] = "",
    resolution: Annotated[str, Form()] = DEFAULT_RESOLUTION,
    image: Annotated[list[UploadFile] | None, File()] = None,
    image_manifest: Annotated[str, Form()] = "[]",
    video: Annotated[list[UploadFile] | None, File()] = None,
    video_manifest: Annotated[str, Form()] = "[]",
) -> LongCloneQueued:
    provider = provider or settings.aigen.default_provider
    if provider not in TARGETS:
        raise HTTPException(status_code=400, detail="That provider cannot clone")
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    resolution = resolution.upper()
    if resolution not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Unknown resolution")
    root = assets_root_for(request)
    user_id = AuthMiddleware.owner_id(request)
    image_path = _one(
        build_ordered_paths(image_manifest, image or [], root, user_id=user_id),
        "image",
        "subject image",
    )
    video_path = _one(
        build_ordered_paths(video_manifest, video or [], root, user_id=user_id),
        "video",
        "video",
    )
    seconds, shape = _measure(video_path)
    longest = settings.clone_long.max_video_s
    if seconds > longest:
        raise HTTPException(
            status_code=400,
            detail=f"The video is {seconds:.0f} s; the longest is {longest} s",
        )
    try:
        clone = LongClone.prepare(
            provider=provider,
            prompt=prompt.strip() or settings.web.clone_default_prompt,
            resolution=resolution,
            image=image_path,
            video=video_path,
            seconds=seconds,
            shape=shape,
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    params = clone.params.model_dump()
    job_id = create_job(TASK, params, actor=AuthMiddleware.actor(request))
    enqueue(
        run_job,
        job_id,
        run_long_clone,
        {
            "job_id": job_id,
            "api_key": api_key.strip(),
            "assets_root": str(root),
            **params,
        },
        job_timeout=clone.time_limit(),
    )
    return LongCloneQueued(job_id=job_id)


add_job_routes(
    router,
    LABEL,
    lambda job: (
        "Generating video… this can take a few minutes."
        if job.params.get("pieces", 1) == 1
        else f"Generating {job.params['pieces']} pieces… this takes a while."
    ),
    back_url="/clone-long",
    back_label="clone another",
    show_inputs=True,
)
