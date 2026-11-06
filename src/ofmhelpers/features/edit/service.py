"""What /edit does behind its routes (web/routers/video_tools/edit.py).

A video is opened from the person's part of the asset store (a path /clone
hands over, or one just uploaded) if it is at most `edit_max_input_s` long;
the page plays it and shows a strip of its frames (`strip`, made once per
video and kept a day with the previews; one request makes it, the others
wait for that file). Cut queues a job on the edit worker (`QueueName.EDIT`:
one at a time, its own CPU and RAM cap): a part of at most
`edit_max_part_s`, re-encoded with its short side capped
(`media/trim.py`). One cut in line per person.

Nothing /edit makes is kept: uploads and cuts go to `.edit/` in the
person's part of the store, which the Reuse pickers leave out (refs.py),
and the hourly sweep (cleanup.py) deletes them after `edit_keep_s` (a day)
unless a running job still names one. Clone it hands the cut to /clone as
`?video=`; Download downloads it.

Every message is worded for the page, never a file name or ffmpeg's text
(that goes to the log).
"""

import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import UploadFile
from pydantic import BaseModel

from ofmhelpers.cache.queue import JobQueue
from ofmhelpers.config import settings
from ofmhelpers.config.settings import QueueName
from ofmhelpers.db.stores.jobs import get_job, list_jobs_page, start_job
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.log import get_logger
from ofmhelpers.media import trim
from ofmhelpers.media.video_probe import duration_s

logger = get_logger(__name__)

PREFIX = "/edit"
TASK = "edit_cut"
# Where /edit keeps what it makes, in each person's part of the store.
STAGING = ".edit"

# What ffmpeg/ffprobe can fail with (`media.ffmpeg` raises RuntimeError on
# a failure or a timeout).
_FFMPEG_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)

_GONE = "That video is gone: pick it again"
_UNREADABLE = "Could not read this video: pick another one"


class EditRefusedError(Exception):
    """Nothing was opened or cut; the message says why (shown on the page)."""


class EditVideo(BaseModel):
    """A stored video, as the page shows it."""

    path: str
    name: str
    seconds: float
    url: str


class EditPage(BaseModel):
    """/edit's page: the video, or why it did not open (None: none asked)."""

    video: EditVideo | None = None
    problem: str | None = None


class CutState(BaseModel):
    """A cut job as the page polls it: in line (`ahead` jobs before it),
    cutting, done (`video`) or failed (`error`)."""

    status: Literal["running", "done", "failed"]
    ahead: int | None = None
    video: EditVideo | None = None
    error: str | None = None


def staging(assets_root: Path) -> Path:
    return assets_root / STAGING


def _shown_name(path: Path) -> str:
    return path.name.split("__", 1)[-1]


def _touch(path: Path) -> None:
    """A deduped upload or cut is "new" again: its day starts now."""
    try:
        os.utime(path)
    except OSError:
        logger.warning("could not touch %s", path, exc_info=True)


def _stored_video(video: str, assets_root: Path) -> Path:
    # The asset store still lives in the routers package (see
    # features/clone/service.card).
    from ofmhelpers.web.routers.task_helpers import keep_files

    kept = keep_files([video], assets_root, "video")
    if not kept:
        raise EditRefusedError(_GONE)
    return Path(kept[0])


def _seconds(path: Path) -> float:
    try:
        return duration_s(path, settings.web.ffmpeg_timeout_s)
    except _FFMPEG_ERRORS as exc:
        logger.warning("could not read the length of %s", path, exc_info=True)
        raise EditRefusedError(_UNREADABLE) from exc


def _video(path: Path, seconds: float) -> EditVideo:
    return EditVideo(
        path=str(path),
        name=_shown_name(path),
        seconds=seconds,
        url=f"/refs/file?path={quote(str(path))}",
    )


def _span(seconds: float) -> str:
    """3 s, 10 min."""
    return f"{seconds:.0f} s" if seconds < 60 else f"{seconds / 60:.0f} min"  # noqa: PLR2004


def _opened(path: Path) -> EditVideo:
    seconds = _seconds(path)
    limit = settings.web.edit_max_input_s
    if seconds > limit:
        msg = f"Videos up to {_span(limit)} open here: this one is {_span(seconds)}"
        raise EditRefusedError(msg)
    return _video(path, seconds)


def open_video(video: str, assets_root: Path) -> EditVideo:
    return _opened(_stored_video(video, assets_root))


def page(video: str | None, assets_root: Path) -> EditPage:
    """The page for `?video=`: a gone, unreadable or too long one opens the
    page empty with the reason, never an error page."""
    if not video:
        return EditPage()
    try:
        return EditPage(video=open_video(video, assets_root))
    except EditRefusedError as exc:
        return EditPage(problem=str(exc))


def upload(file: UploadFile, assets_root: Path) -> EditVideo:
    """A video from the person's computer into `.edit/`, to open here."""
    from ofmhelpers.web.routers.task_helpers import save_asset

    if classify_kind(file.filename or "") != "video":
        msg = "Pick a video file"
        raise EditRefusedError(msg)
    cap_mb = settings.web.edit_max_upload_mb
    if file.size is not None and file.size > cap_mb * 1024 * 1024:
        msg = f"Videos up to {cap_mb} MB open here"
        raise EditRefusedError(msg)
    path = Path(save_asset(file, staging(assets_root)))
    _touch(path)
    return _opened(path)


@lru_cache(maxsize=256)
def _making(dest: Path) -> threading.Lock:  # noqa: ARG001 -- the cache key
    """One lock per strip being made: a second request for the same video
    waits for the first one's file instead of decoding it again."""
    return threading.Lock()


def strip(video: str, assets_root: Path) -> Path:
    """The timeline's frames of `video` as one image, made on first ask."""
    path = _stored_video(video, assets_root)
    web = settings.web
    digest = path.name.split("__", 1)[0]
    dest = (
        previews_dir()
        / "strips"
        / f"{digest}_{web.edit_strip_frames}x{web.edit_strip_height}.jpg"
    )
    with _making(dest):
        if dest.is_file():
            return dest
        seconds = _seconds(path)
        try:
            return trim.filmstrip(
                path,
                seconds,
                dest,
                frames=web.edit_strip_frames,
                height=web.edit_strip_height,
                keyframes_only=seconds > web.edit_strip_all_frames_s,
                threads=web.edit_threads,
                timeout_s=web.edit_strip_timeout_s,
            )
        except _FFMPEG_ERRORS as exc:
            logger.warning("could not make the strip of %s", path, exc_info=True)
            raise EditRefusedError(_UNREADABLE) from exc


def _part(start: float, end: float, seconds: float) -> tuple[float, float]:
    """The asked part within the video, refused when too short or long."""
    web = settings.web
    end = min(end, seconds)
    if end - start < web.edit_min_s:
        msg = f"Pick at least {web.edit_min_s:g} s"
        raise EditRefusedError(msg)
    if end - start > web.edit_max_part_s:
        msg = f"Pick at most {web.edit_max_part_s:g} s"
        raise EditRefusedError(msg)
    return start, end


def queue_cut(
    video: str,
    start: float,
    end: float,
    assets_root: Path,
    actor: str,
    run: Callable[..., Any],
) -> str:
    """The cut's job on the edit worker. `run`: the worker's entry point
    (routers/video_tools/edit.py `run_cut`, a path RQ can import)."""
    path = _stored_video(video, assets_root)
    start, end = _part(start, end, _seconds(path))
    _, waiting = list_jobs_page({TASK}, 0, 1, actor, status="running")
    if waiting:
        msg = "Your last cut is still running: wait for it"
        raise EditRefusedError(msg)
    params = {"video": str(path), "start": start, "end": end}
    return start_job(
        TASK,
        params,
        run,
        {**params, "assets_root": str(assets_root)},
        actor=actor,
        queue=QueueName.EDIT,
        job_timeout=settings.web.edit_cut_timeout_s + 60,
    )


def _make_cut(path: Path, start: float, end: float, root: Path) -> Path:
    """The part, cut in a work folder and stored in `.edit/`."""
    from ofmhelpers.web.routers.task_helpers import register_generated_asset

    web = settings.web
    work = Path(settings.infra.uploads_root) / STAGING
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        made = Path(tmp) / f"{Path(_shown_name(path)).stem}_cut.mp4"
        trim.cut(
            path,
            start,
            end,
            made,
            short_side=web.edit_short_side,
            threads=web.edit_threads,
            timeout_s=web.edit_cut_timeout_s,
        )
        stored = register_generated_asset(made, staging(root))
    if stored is None:
        msg = "could not store the cut"
        raise OSError(msg)
    return stored


def run_cut(video: str, start: float, end: float, assets_root: str) -> dict[str, Any]:
    """The cut itself (on the edit worker), as `<name>_cut.mp4` in `.edit/`.
    Any failure is one sentence for the page; the cause goes to the log."""
    root = Path(assets_root)
    path = _stored_video(video, root)
    try:
        stored = _make_cut(path, start, end, root)
        _touch(stored)
        return _video(stored, _seconds(stored)).model_dump()
    except EditRefusedError:
        raise
    except Exception as exc:
        logger.warning("could not cut %s %.1f-%.1f", path, start, end, exc_info=True)
        msg = "Could not cut this video: try again"
        raise EditRefusedError(msg) from exc


def cut_state(job_id: str, actor: str) -> CutState:
    job = get_job(job_id, actor)
    if job is None:
        return CutState(status="failed", error="This cut is gone: cut again")
    if job.status == "done" and isinstance(job.result, dict):
        return CutState(status="done", video=EditVideo.model_validate(job.result))
    if job.status == "failed":
        return CutState(status="failed", error=job.error or "Could not cut: try again")
    return CutState(status="running", ahead=JobQueue(QueueName.EDIT).line().get(job_id))
