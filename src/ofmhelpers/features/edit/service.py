"""What /edit does behind its routes (web/routers/video_tools/edit.py).

A video is opened from the person's part of the asset store (a path /clone
hands over, or one just uploaded) if it is at most `edit_max_input_s` long.
Opening it (and an upload) queues `edit_prepare` on the edit worker: a light
copy the page plays (`media/trim.py` `proxy`) and the strip of its frames,
made once per video and kept a day with the previews (`prepared_dir`); the
page plays the original until they are there (`prepared_state`). Nothing
ffmpeg does runs in a page request but a frame grab (one frame). Cut queues
a job on the edit worker (`QueueName.EDIT`: one at a time, its own CPU and
RAM cap): a part of at most `edit_max_part_s`, cropped and sped up or down
as asked (`options.py`), re-encoded with its short side capped. One cut in
line per person; it comes out turned / mirrored as picked, then cropped,
at the size picked (`Size`, up or down).
Frame (`frame`) is the frame the page shows, cropped and at that size, as a
JPEG download. The page steps frame by frame through the frame times
`run_prepare` reads off the light copy (`FrameTimes`).

Nothing /edit makes is kept: uploads and cuts go to `.edit/` in the
person's part of the store, which the Reuse pickers leave out (refs.py),
and the hourly sweep (cleanup.py) deletes them after `edit_keep_s` (a day)
unless a running job still names one. Clone it hands the cut to /clone as
`?video=`; Download downloads it.

Every message is worded for the page, never a file name or ffmpeg's text
(that goes to the log).
"""

import hashlib
import os
import re
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
from redis.exceptions import RedisError

from ofmhelpers.cache.queue import JobQueue
from ofmhelpers.config import settings
from ofmhelpers.config.settings import QueueName
from ofmhelpers.db.stores.jobs import drop_job, get_job, list_jobs_page, start_job
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.edit.options import Framing
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.log import get_logger
from ofmhelpers.media import trim
from ofmhelpers.media.video_probe import duration_s, frame_rate, frame_times
from ofmhelpers.schemas import Job

logger = get_logger(__name__)

PREFIX = "/edit"
TASK = "edit_cut"
PREPARE_TASK = "edit_prepare"
# Where /edit keeps what it makes, in each person's part of the store.
STAGING = ".edit"

# What ffmpeg/ffprobe can fail with (`media.ffmpeg` raises RuntimeError on
# a failure or a timeout).
_FFMPEG_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)

_GONE = "That video is gone: pick it again"
_UNREADABLE = "Could not read this video: pick another one"
_NOT_PREPARED = "Could not make the light copy: open the video again"
_NO_FRAME = "Could not save this frame: try again"
_BUSY = "Busy saving frames: try again"
_REPLACED = "Another video was opened"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class EditRefusedError(Exception):
    """Nothing was opened or cut; the message says why (shown on the page)."""


class EditVideo(BaseModel):
    """A stored video, as the page shows it."""

    path: str
    name: str
    seconds: float
    url: str
    # Frames per second (the page's one-frame steps); None: ffprobe had none.
    fps: float | None = None


class Prepared(BaseModel):
    """The page's light copy and strip of a video as the page polls them:
    being made (`ahead` jobs before it), there (`proxy_url`, `strip_url`)
    or failed (the page keeps playing the original)."""

    status: Literal["running", "done", "failed"]
    ahead: int | None = None
    proxy_url: str | None = None
    strip_url: str | None = None
    frames_url: str | None = None


class FrameTimes(BaseModel):
    """Every frame's start time (s) of a video, in order: the page's frame
    steps go frame by frame, even when a phone spaced them unevenly."""

    times: list[float]


class EditPage(BaseModel):
    """/edit's page: the video, or why it did not open (None: none asked)."""

    video: EditVideo | None = None
    prepared: Prepared | None = None
    problem: str | None = None


class FrameGrab(BaseModel):
    """One frame as a JPEG and the name it downloads as."""

    data: bytes
    name: str


class CutState(BaseModel):
    """A cut job as the page polls it: in line (`ahead` jobs before it),
    cutting, done (`video`) or failed (`error`)."""

    status: Literal["running", "done", "failed"]
    ahead: int | None = None
    video: EditVideo | None = None
    error: str | None = None


def staging(assets_root: Path) -> Path:
    return assets_root / STAGING


def prepared_dir() -> Path:
    """Where the light copies and strips are kept (a day: cleanup.py)."""
    return previews_dir() / "edit"


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


def _fps(path: Path) -> float | None:
    try:
        return frame_rate(path, settings.web.ffmpeg_timeout_s)
    except _FFMPEG_ERRORS:
        logger.warning("could not read the frame rate of %s", path, exc_info=True)
        return None


def _video(path: Path, seconds: float, fps: float | None = None) -> EditVideo:
    return EditVideo(
        path=str(path),
        name=_shown_name(path),
        seconds=seconds,
        url=f"/refs/file?path={quote(str(path))}",
        fps=fps,
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
    return _video(path, seconds, _fps(path))


def open_video(video: str, assets_root: Path) -> EditVideo:
    return _opened(_stored_video(video, assets_root))


def page(
    video: str | None, assets_root: Path, actor: str, run: Callable[..., Any]
) -> EditPage:
    """The page for `?video=`: a gone, unreadable or too long one opens the
    page empty with the reason, never an error page. An opened one has its
    light copy and strip queued (`run`: the worker's entry point)."""
    if not video:
        return EditPage()
    try:
        opened = open_video(video, assets_root)
    except EditRefusedError as exc:
        return EditPage(problem=str(exc))
    prepared = _queue_prepare_quietly(Path(opened.path), assets_root, actor, run)
    return EditPage(video=opened, prepared=prepared)


def upload(
    file: UploadFile, assets_root: Path, actor: str, run: Callable[..., Any]
) -> EditVideo:
    """A video from the person's computer into `.edit/`, to open here; its
    light copy and strip are queued at once (`run`, as for `page`)."""
    from ofmhelpers.web.routers.task_helpers import save_asset

    if classify_kind(file.filename or "") != "video":
        msg = "Pick a video file"
        raise EditRefusedError(msg)
    cap_mb = settings.web.edit_max_upload_mb
    if file.size is not None and file.size > cap_mb * 1024 * 1024:
        msg = f"Videos up to {cap_mb} MB open here"
        raise EditRefusedError(msg)
    path = _stored_video(save_asset(file, staging(assets_root)), assets_root)
    _touch(path)
    opened = _opened(path)
    _queue_prepare_quietly(path, assets_root, actor, run)
    return opened


PreparedKind = Literal["proxy", "strip", "frames"]


def _key(path: Path) -> str:
    """What the prepared files of `path` are named by: its content digest
    (`<sha256>__name`, every upload and download), else a hash of where it is
    and its size -- two same-named files never share a copy."""
    head, sep, _ = path.name.partition("__")
    if sep and _SHA256.fullmatch(head):
        return head
    where = f"{path.resolve()}:{path.stat().st_size}"
    return hashlib.sha256(where.encode()).hexdigest()


def _prepared_files(path: Path) -> dict[PreparedKind, Path]:
    """The light copy, the strip and the frame times of `path`, by its
    content digest."""
    web = settings.web
    digest = _key(path)
    folder = prepared_dir()
    strip = f"{digest}_{web.edit_strip_frames}x{web.edit_strip_height}.jpg"
    return {
        "proxy": folder / f"{digest}_{web.edit_proxy_short_side}p.mp4",
        "strip": folder / strip,
        "frames": folder / f"{digest}_frames.json",
    }


def _ready(path: Path) -> Prepared | None:
    files = _prepared_files(path).values()
    if not all(f.is_file() for f in files):
        return None
    # In use: the sweep's day starts again (cleanup.py).
    for f in files:
        _touch(f)
    video = quote(str(path))
    return Prepared(
        status="done",
        proxy_url=f"{PREFIX}/proxy?video={video}",
        strip_url=f"{PREFIX}/strip?video={video}",
        frames_url=f"{PREFIX}/frames?video={video}",
    )


def _prepares(actor: str, status: str) -> list[Job]:
    jobs, _ = list_jobs_page({PREPARE_TASK}, 0, 50, actor, status=status)
    return jobs


def _running_prepare(path: Path, actor: str) -> Job | None:
    jobs = _prepares(actor, "running")
    return next((j for j in jobs if j.params.get("video") == str(path)), None)


def _unreadable(path: Path, actor: str) -> bool:
    """A prepare of `path` already failed reading it (not one dropped or cut
    off by a restart): a next one would fail the same way, and a long video
    holds the edit worker up to its time limit each time."""
    return any(
        j.params.get("video") == str(path) and j.error == _UNREADABLE
        for j in _prepares(actor, "failed")
    )


def _drop_older(path: Path, actor: str) -> None:
    """The person's prepares still waiting for another video leave the line:
    they opened this one, and cuts wait behind every prepare (one worker)."""
    queue = JobQueue(QueueName.EDIT)
    for job in _prepares(actor, "running"):
        if job.params.get("video") != str(path) and queue.drop(job.id):
            drop_job(job.id, _REPLACED)


def _in_line(job_id: str) -> Prepared:
    ahead = JobQueue(QueueName.EDIT).line().get(job_id)
    return Prepared(status="running", ahead=ahead)


def queue_prepare(
    path: Path, assets_root: Path, actor: str, run: Callable[..., Any]
) -> Prepared:
    """The light copy and strip of `path`: there, or queued on the edit
    worker once (a second open of the same video joins the first job)."""
    ready = _ready(path)
    if ready is not None:
        return ready
    job = _running_prepare(path, actor)
    if job is not None:
        return _in_line(job.id)
    if _unreadable(path, actor):
        return Prepared(status="failed")
    _drop_older(path, actor)
    params = {"video": str(path)}
    job_id = start_job(
        PREPARE_TASK,
        params,
        run,
        {**params, "assets_root": str(assets_root)},
        actor=actor,
        queue=QueueName.EDIT,
        job_timeout=settings.web.edit_prepare_timeout_s + 60,
    )
    return _ready(path) or _in_line(job_id)


def _queue_prepare_quietly(
    path: Path, assets_root: Path, actor: str, run: Callable[..., Any]
) -> Prepared:
    """`queue_prepare` for a page or an upload: the queue down plays the
    original (failed), never a 500 instead of the page."""
    try:
        return queue_prepare(path, assets_root, actor, run)
    except RedisError:
        logger.warning("could not queue the light copy of %s", path, exc_info=True)
        return Prepared(status="failed")


def prepared_state(video: str, assets_root: Path, actor: str) -> Prepared:
    """What the page polls: there, being made, or failed (no job makes it)."""
    path = _stored_video(video, assets_root)
    ready = _ready(path)
    if ready is not None:
        return ready
    job = _running_prepare(path, actor)
    if job is not None:
        return _in_line(job.id)
    # The job may have just finished between the two looks.
    return _ready(path) or Prepared(status="failed")


def prepared_file(video: str, assets_root: Path, kind: PreparedKind) -> Path | None:
    """The light copy, strip or frame times of `video` once made, else None."""
    found = _prepared_files(_stored_video(video, assets_root))[kind]
    return found if found.is_file() else None


def _write_frame_times(proxy: Path, dest: Path) -> None:
    """The copy's frame times (the source's: `-fps_mode passthrough`)."""
    times = frame_times(proxy, settings.web.ffmpeg_timeout_s)
    tmp = dest.with_name(f".part-{os.getpid()}-{dest.name}")
    try:
        tmp.write_text(FrameTimes(times=times).model_dump_json(), encoding="utf-8")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def run_prepare(video: str, assets_root: str) -> dict[str, Any]:
    """Makes the light copy, then its strip and frame times (on the edit
    worker); a part already made is kept."""
    path = _stored_video(video, Path(assets_root))
    files = _prepared_files(path)
    proxy, strip = files["proxy"], files["strip"]
    web = settings.web
    try:
        if not proxy.is_file():
            trim.proxy(
                path,
                proxy,
                short_side=web.edit_proxy_short_side,
                gop=web.edit_proxy_gop,
                crf=web.edit_proxy_crf,
                threads=web.edit_threads,
                timeout_s=web.edit_prepare_timeout_s,
            )
        if not strip.is_file():
            seconds = _seconds(proxy)
            trim.filmstrip(
                proxy,
                seconds,
                strip,
                frames=web.edit_strip_frames,
                height=web.edit_strip_height,
                keyframes_only=seconds > web.edit_strip_all_frames_s,
                threads=web.edit_threads,
                timeout_s=web.edit_strip_timeout_s,
            )
        if not files["frames"].is_file():
            _write_frame_times(proxy, files["frames"])
    except _FFMPEG_ERRORS as exc:
        logger.warning("could not prepare %s", path, exc_info=True)
        raise EditRefusedError(_why_not_prepared(path, exc)) from exc
    return {kind: str(f) for kind, f in files.items()}


def _why_not_prepared(path: Path, exc: BaseException) -> str:
    """`_UNREADABLE` (never tried again for that video: `_unreadable`) when
    the video itself is the cause -- ffprobe cannot read it, or ffmpeg ran
    out of its time limit, which the next try would too; anything else
    (disk full, killed for memory) may pass next time."""
    if "timed out" in str(exc):
        return _UNREADABLE
    try:
        _seconds(path)
    except EditRefusedError:
        return _UNREADABLE
    return _NOT_PREPARED


def _picture(framing: Framing) -> str | None:
    """The turn, then the crop (on the turned picture, as the page draws
    it); None when neither is asked."""
    ratio = framing.aspect.ratio
    cropped = ratio is not None or framing.scale < 1
    crop = (
        trim.crop_filter(ratio, framing.x, framing.y, framing.scale)
        if cropped
        else None
    )
    parts = [trim.turn_filter(framing.turn, framing.mirror), crop]
    return ",".join(p for p in parts if p) or None


def _frame_name(path: Path, at: float) -> str:
    minutes = int(at // 60)
    stem = Path(_shown_name(path)).stem
    return f"{stem}_{minutes:02d}m{at - minutes * 60:04.1f}s.jpg"


@lru_cache(maxsize=1)
def _grabbing() -> threading.BoundedSemaphore:
    """`edit_frame_parallel` frame grabs at once in this process."""
    return threading.BoundedSemaphore(settings.web.edit_frame_parallel)


def frame(video: str, at: float, framing: Framing, assets_root: Path) -> FrameGrab:
    """The frame at `at` seconds, turned, cropped and sized as asked; a grab
    waits `edit_frame_wait_s` for a free turn (`_grabbing`), then is refused:
    a sync route waiting holds one of the app's 40 threads."""
    path = _stored_video(video, assets_root)
    at = min(max(at, 0.0), _seconds(path))
    web = settings.web
    turn = _grabbing()
    if not turn.acquire(timeout=web.edit_frame_wait_s):
        raise EditRefusedError(_BUSY)
    try:
        return _grab(path, at, framing)
    finally:
        turn.release()


def _grab(path: Path, at: float, framing: Framing) -> FrameGrab:
    web = settings.web
    try:
        data = trim.frame(
            path,
            at,
            short_side=framing.size.short_side,
            threads=web.edit_threads,
            timeout_s=web.edit_frame_timeout_s,
            picture=_picture(framing),
        )
    except _FFMPEG_ERRORS as exc:
        logger.warning("could not grab %s at %.3f", path, at, exc_info=True)
        raise EditRefusedError(_NO_FRAME) from exc
    if not data:
        logger.warning("no frame in %s at %.3f", path, at)
        raise EditRefusedError(_NO_FRAME)
    return FrameGrab(data=data, name=_frame_name(path, at))


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
    framing: Framing,
    speed: float,
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
    params = {
        "video": str(path),
        "start": start,
        "end": end,
        "turn": framing.turn,
        "mirror": framing.mirror,
        "aspect": framing.aspect.value,
        "size": framing.size.value,
        "scale": framing.scale,
        "x": framing.x,
        "y": framing.y,
        "speed": speed,
    }
    return start_job(
        TASK,
        params,
        run,
        {**params, "assets_root": str(assets_root)},
        actor=actor,
        queue=QueueName.EDIT,
        job_timeout=settings.web.edit_cut_timeout_s + 60,
        # Someone watches "Cutting…": ahead of the pages' light copies on
        # the one edit worker, it waits at most for the job being run.
        at_front=True,
    )


def _make_cut(
    path: Path, start: float, end: float, framing: Framing, speed: float, root: Path
) -> Path:
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
            short_side=framing.size.short_side,
            threads=web.edit_threads,
            timeout_s=web.edit_cut_timeout_s,
            picture=_picture(framing),
            speed=speed,
        )
        stored = register_generated_asset(made, staging(root))
    if stored is None:
        msg = "could not store the cut"
        raise OSError(msg)
    return stored


def run_cut(
    video: str,
    start: float,
    end: float,
    assets_root: str,
    turn: int = 0,
    mirror: bool = False,
    aspect: str = "original",
    size: str = "1080",
    scale: float = 1.0,
    x: float = 0.5,
    y: float = 0.5,
    speed: float = 1.0,
) -> dict[str, Any]:
    """The cut itself (on the edit worker), as `<name>_cut.mp4` in `.edit/`.
    Any failure is one sentence for the page; the cause goes to the log."""
    root = Path(assets_root)
    path = _stored_video(video, root)
    framing = Framing.model_validate(
        {
            "turn": turn,
            "mirror": mirror,
            "aspect": aspect,
            "size": size,
            "scale": scale,
            "x": x,
            "y": y,
        }
    )
    try:
        stored = _make_cut(path, start, end, framing, speed, root)
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
