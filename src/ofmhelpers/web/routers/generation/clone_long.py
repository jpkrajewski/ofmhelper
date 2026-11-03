"""
/clone-long: one subject image + one video of any length (features/clone_long).
The page prices the pick live; `/run` measures the video itself, re-prices
it, checks every piece against the model and only then queues the job --
the stored price is the server's, never the browser's. Results are the
shared gallery card, paged like /clone's (`/clone-long/gallery`,
gallery-scroll.js); the status page and files come from `add_job_routes`
(only the session's own jobs).
"""

import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import start_job
from ofmhelpers.features.clone_long import (
    RESOLUTIONS,
    TARGETS,
    LongClone,
    Shape,
    VideoTools,
    rate,
    run_long_clone,
)
from ofmhelpers.schemas import Job, ProviderChoice, TaskCard
from ofmhelpers.web.dependencies import Actor, ApiKeys, AssetsRoot, OwnerId
from ofmhelpers.web.forms import Files, PaidForm, Stripped, Upper
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    build_ordered_paths,
    classify_kind,
    job_assets,
    keep_videos,
)
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/clone-long", tags=["clone-long"])

TASK = "clone_long"
LABEL = "Clone long"
FILES_PREFIX = f"{router.prefix}/files"
DEFAULT_RESOLUTION = "720P"

gallery = Gallery({TASK})


class LongCloneForm(PaidForm):
    prompt: Stripped = Field("", max_length=2000)
    resolution: Upper = DEFAULT_RESOLUTION
    image: Files
    image_manifest: str = "[]"
    video: Files
    video_manifest: str = "[]"


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
def page(
    request: Request,
    actor: Actor,
    api_keys: ApiKeys,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    first = gallery.page(actor)
    # One video: a download sends its first (clone-long clones one video).
    prefill = keep_videos(video or [], assets_root)[:1]
    return render(
        request,
        "clone_long.html",
        gallery=[card(j) for j in first.jobs],
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(actor),
        prefill_video=prefill,
        providers=ProviderChoice.all(list(TARGETS)),
        selected_provider=settings.aigen.default_provider,
        api_keys=api_keys,
        rates={name: rate(name).model_dump() for name in TARGETS},
        max_piece_s=min(t.max_reference_s for t in TARGETS.values()),
        max_video_s=settings.clone_long.max_video_s,
        resolutions=RESOLUTIONS,
        default_resolution=DEFAULT_RESOLUTION,
        default_prompt=settings.web.clone_default_prompt,
    )


@router.get("/gallery")
def gallery_page(request: Request, actor: Actor, query: GalleryParams) -> Response:
    """The next page of cards, for gallery-scroll.js; `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(actor, query)
    return render(
        request,
        "clone_long_gallery_fragment.html",
        gallery=[card(j) for j in found.jobs],
        next_offset=found.next_offset,
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
    form: Annotated[LongCloneForm, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> LongCloneQueued:
    if form.provider not in TARGETS:
        raise HTTPException(status_code=400, detail="That provider cannot clone")
    if not form.api_key:
        raise HTTPException(status_code=400, detail="API key is required")
    if form.resolution not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Unknown resolution")
    image_path = _one(
        build_ordered_paths(
            form.image_manifest, form.image, assets_root, user_id=owner_id
        ),
        "image",
        "subject image",
    )
    video_path = _one(
        build_ordered_paths(
            form.video_manifest, form.video, assets_root, user_id=owner_id
        ),
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
    clone = LongClone.prepare(
        provider=form.provider,
        prompt=form.prompt or settings.web.clone_default_prompt,
        resolution=form.resolution,
        image=image_path,
        video=video_path,
        seconds=seconds,
        shape=shape,
    )
    params = clone.params.model_dump()
    job_id = start_job(
        TASK,
        params,
        run_long_clone,
        {"api_key": form.api_key, "assets_root": str(assets_root), **params},
        actor=actor,
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
