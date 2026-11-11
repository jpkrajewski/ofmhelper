"""/download-media: any links -- videos, photo posts, carousels -- in one go.
The downloader picks yt-dlp or gallery-dl per link
(`ofmhelpers.integrations.downloaders.media`), so nobody has to choose."""

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

from ofmhelpers.db.stores import cookies as login_cookies
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import (
    register_generated_asset,
    register_grouped_results,
)
from ofmhelpers.features.downloads.service import Redirect, queue_downloads
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.integrations.downloaders.media import download_all
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult
lazy from ofmhelpers.schemas import JobQueued

TASK = "download_media"


class SendTarget(StrEnum):
    """Where a download's video can be sent on."""

    CLONE = "clone"
    CLONE_LONG = "clone-long"

    @property
    def page(self) -> str:
        return f"/{self.value}"

    @property
    def one_only(self) -> bool:
        """/clone-long clones a single video, /clone one run per video."""
        return self is SendTarget.CLONE_LONG


def run_downloads(
    urls: list[str], assets_root: str, user_id: int | None = None
) -> list[DownloadResult]:
    """Runs in the background, logged in as the user who started it (their
    own cookies.txt, read here: never in the queued job); run_job stores
    the results as JSON."""
    results = download_all(urls, cookies=login_cookies.for_user(user_id))
    register_grouped_results(results, Path(assets_root))
    return results


def start(pasted: str, actor: str, owner_id: int, assets_root: Path) -> JobQueued:
    return queue_downloads(TASK, pasted, run_downloads, actor, owner_id, assets_root)


def _kept_videos(
    paths: list[str | None], assets_root: Path, *, one_only: bool
) -> list[str]:
    """A download's files live in the download area, not the asset store that
    /clone's picker reuses from; each video is registered into this session's
    store (as /reels does) so clone opens with it picked."""
    videos: list[str] = []
    for path in paths:
        if path is None:
            continue
        src = Path(path)
        if not src.is_file() or classify_kind(src.name) != "video":
            continue
        kept = register_generated_asset(src, assets_root)
        if kept is not None:
            videos.append(str(kept.resolve()))
            if one_only:
                break
    return videos


def send(
    job_id: str, target: str, index: int | None, actor: str, assets_root: Path
) -> Redirect:
    """A finished download's video handed to /clone or /clone-long: the URL to
    open with it already picked. `index`: that file, what a card sends; none:
    all its videos (/clone) or its first (/clone-long). The job must be the
    asker's own (its actor); its video files are re-checked against their
    store before being handed over."""
    if target not in SendTarget:
        msg = "Unknown target"
        raise RefusedError(msg)
    chosen = SendTarget(target)
    job = get_job(job_id, actor=actor)
    if job is None:
        msg = "No such download"
        raise NotFoundError(msg)
    paths = job.local_paths()
    if index is not None:
        paths = paths[index : index + 1]
    videos = _kept_videos(paths, assets_root, one_only=chosen.one_only)
    if not videos:
        msg = "This download has no video"
        raise RefusedError(msg)
    return Redirect(
        redirect=chosen.page + "?" + urlencode([("video", v) for v in videos])
    )
