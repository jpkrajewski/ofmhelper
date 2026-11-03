"""/download-media: any links -- videos, photo posts, carousels -- in one go.
The downloader picks yt-dlp or gallery-dl per link
(`ofmhelpers.integrations.downloaders.media`), so nobody has to choose. Replaced the
separate Download videos / Download images tools on /download-assets; their
routes stay for their past jobs."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request

from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.integrations.downloaders.media import download_all
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.downloads.shared import queue_downloads
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    classify_kind,
    register_generated_asset,
    register_grouped_results,
)

TASK = "download_media"

router = APIRouter(prefix="/download-media", tags=["download-media"])

# Where a download's video can be sent on: the page, and whether it takes one
# video or many (clone-long clones a single video, /clone one run per video).
_SEND_TARGETS = {"clone": ("/clone", False), "clone-long": ("/clone-long", True)}


def _run_downloads(
    urls: list[str], assets_root: str, user_id: int | None = None
) -> list[DownloadResult]:
    """Runs in the background, logged in as the user who started it (their
    own cookies.txt, read here: never in the queued job); run_job stores
    the results as JSON."""
    results = download_all(urls, cookies=login_cookies.for_user(user_id))
    register_grouped_results(results, Path(assets_root))
    return results


@router.post("/run")
def run(request: Request, urls: Annotated[str, Form()]) -> JobQueued:
    return queue_downloads(request, TASK, urls, _run_downloads)


@router.post("/send")
def send(
    request: Request,
    job_id: Annotated[str, Form()],
    target: Annotated[str, Form()],
):
    """Hand a finished download's video(s) to /clone or /clone-long: answers
    the URL to open with them already picked. The job must be this session's
    own (its actor); its video files are re-checked against this session's
    store before being handed over."""
    chosen = _SEND_TARGETS.get(target)
    if chosen is None:
        raise HTTPException(status_code=400, detail="Unknown target")
    base, one_only = chosen
    job = get_job(job_id, actor=AuthMiddleware.actor(request))
    if job is None:
        raise HTTPException(status_code=404, detail="No such download")
    # A download's files live in the download area, not the asset store that
    # /clone's picker reuses from; register each video into this session's
    # store (as /reels does) so clone opens with it picked.
    assets_root = assets_root_for(request)
    videos: list[str] = []
    for path in job.local_paths():
        if path is None:
            continue
        src = Path(path)
        if not src.is_file() or classify_kind(src.name) != "video":
            continue
        kept = register_generated_asset(src, assets_root)
        if kept is not None:
            videos.append(str(kept.resolve()))
    if not videos:
        raise HTTPException(status_code=400, detail="This download has no video")
    if one_only:
        videos = videos[:1]
    return {"redirect": base + "?" + urlencode([("video", v) for v in videos])}


add_job_routes(
    router,
    "Download",
    lambda job: f"Downloading {len(job.params['urls'])} url(s)…",
    back_url="/download-assets",
    back_label="download more",
)
