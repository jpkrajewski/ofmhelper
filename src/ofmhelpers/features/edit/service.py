import hashlib
import os
import re
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
lazy from collections.abc import Callable

from pydantic import BaseModel
from redis.exceptions import RedisError
lazy from fastapi import UploadFile

from ofmhelpers.cache.queue import JobQueue
from ofmhelpers.config import settings
from ofmhelpers.config.settings import QueueName
from ofmhelpers.db.jobs.store import drop_job, get_job, list_jobs_page
from ofmhelpers.features.assets.manifests import keep_files
from ofmhelpers.features.assets.store import (
    EDIT_STAGING,
    register_generated_asset,
    save_asset,
)
from ofmhelpers.features.edit.clips import Clip, checked
from ofmhelpers.features.edit.options import Framing
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.features.refusals import EditRefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.media import trim
from ofmhelpers.media.uniqify import has_audio
from ofmhelpers.media.video_probe import (
    duration_s,
    frame_rate,
    frame_times,
    video_size,
)
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.media.size import Size

logger = get_logger(__name__)

PREFIX = "/edit"
TASK = "edit_cut"
PREPARE_TASK = "edit_prepare"
STAGING = EDIT_STAGING

_FFMPEG_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)

_GONE = "That video is gone: pick it again"
_UNREADABLE = "Could not read this video: pick another one"
_NOT_PREPARED = "Could not make the light copy: open the video again"
_NO_FRAME = "Could not save this frame: try again"
_BUSY = "Busy saving frames: try again"
_REPLACED = "Another video was opened"
_JOIN_FPS = 30.0
_JOIN_MAX_FPS = 60.0
_SHA256 = re.compile(r"[0-9a-f]{64}")


class EditVideo(BaseModel):
    path: str
    name: str
    seconds: float
    url: str
    fps: float | None = None


class Prepared(BaseModel):
    status: Literal["running", "done", "failed"]
    ahead: int | None = None
    proxy_url: str | None = None
    strip_url: str | None = None
    frames_url: str | None = None


class FrameTimes(BaseModel):
    times: list[float]


class EditPage(BaseModel):
    video: EditVideo | None = None
    prepared: Prepared | None = None
    problem: str | None = None


class FrameGrab(BaseModel):
    data: bytes
    name: str


class CutState(BaseModel):
    status: Literal["running", "done", "failed"]
    ahead: int | None = None
    video: EditVideo | None = None
    error: str | None = None


def staging(assets_root: Path) -> Path:
    return assets_root / STAGING


def prepared_dir() -> Path:
    return previews_dir() / "edit"


def _shown_name(path: Path) -> str:
    return path.name.split("__", 1)[-1]


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        logger.warning("could not touch %s", path, exc_info=True)


def _stored_video(video: str, assets_root: Path) -> Path:
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


def upload(
    file: UploadFile, assets_root: Path, actor: str, run: Callable[..., Any]
) -> EditVideo:
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
    head, sep, _ = path.name.partition("__")
    if sep and _SHA256.fullmatch(head):
        return head
    where = f"{path.resolve()}:{path.stat().st_size}"
    return hashlib.sha256(where.encode()).hexdigest()


class _PreparedPaths(BaseModel, frozen=True):
    proxy: Path
    strip: Path
    frames: Path

    def all(self) -> list[Path]:
        return [self.proxy, self.strip, self.frames]

    def of(self, kind: PreparedKind) -> Path:
        return Path(getattr(self, kind))


def _prepared_files(path: Path) -> _PreparedPaths:
    web = settings.web
    digest = _key(path)
    folder = prepared_dir()
    strip = f"{digest}_{web.edit_strip_frames}x{web.edit_strip_height}.jpg"
    return _PreparedPaths(
        proxy=folder / f"{digest}_{web.edit_proxy_short_side}p.mp4",
        strip=folder / strip,
        frames=folder / f"{digest}_frames.json",
    )


class PreparedFiles(BaseModel, frozen=True):
    proxy: str
    strip: str
    frames: str


def _ready(path: Path) -> Prepared | None:
    files = _prepared_files(path).all()
    if not all(f.is_file() for f in files):
        return None
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
    return list_jobs_page({PREPARE_TASK}, 0, 50, actor, status=status).jobs


def _running_prepare(path: Path, actor: str) -> Job | None:
    jobs = _prepares(actor, "running")
    return next((j for j in jobs if j.params.get("video") == str(path)), None)


def _unreadable(path: Path, actor: str) -> bool:
    return any(
        j.params.get("video") == str(path) and j.error == _UNREADABLE
        for j in _prepares(actor, "failed")
    )


def _drop_older(path: Path, actor: str) -> None:
    queue = JobQueue(name=QueueName.EDIT)
    for job in _prepares(actor, "running"):
        if job.params.get("video") != str(path) and queue.drop(job.id):
            drop_job(job.id, _REPLACED)


def _in_line(job_id: str) -> Prepared:
    ahead = JobQueue(name=QueueName.EDIT).line().ahead(job_id)
    return Prepared(status="running", ahead=ahead)


def queue_prepare(
    path: Path, assets_root: Path, actor: str, run: Callable[..., Any]
) -> Prepared:
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
    try:
        return queue_prepare(path, assets_root, actor, run)
    except RedisError:
        logger.warning("could not queue the light copy of %s", path, exc_info=True)
        return Prepared(status="failed")


class Asked(BaseModel, frozen=True):
    video: str
    assets_root: Path
    actor: str

    def page(self, run: Callable[..., Any]) -> EditPage:
        if not self.video:
            return EditPage()
        try:
            opened = open_video(self.video, self.assets_root)
        except EditRefusedError as exc:  # silent: shown on the page
            return EditPage(problem=str(exc))
        path = Path(opened.path)
        prepared = _queue_prepare_quietly(path, self.assets_root, self.actor, run)
        return EditPage(video=opened, prepared=prepared)

    def add(self, run: Callable[..., Any]) -> EditVideo:
        opened = open_video(self.video, self.assets_root)
        _queue_prepare_quietly(Path(opened.path), self.assets_root, self.actor, run)
        return opened

    def prepared(self) -> Prepared:
        path = _stored_video(self.video, self.assets_root)
        ready = _ready(path)
        if ready is not None:
            return ready
        job = _running_prepare(path, self.actor)
        if job is not None:
            return _in_line(job.id)
        return _ready(path) or Prepared(status="failed")


def prepared_file(video: str, assets_root: Path, kind: PreparedKind) -> Path | None:
    found = _prepared_files(_stored_video(video, assets_root)).of(kind)
    return found if found.is_file() else None


def _write_frame_times(proxy: Path, dest: Path) -> None:
    times = frame_times(proxy, settings.web.ffmpeg_timeout_s)
    tmp = dest.with_name(f".part-{os.getpid()}-{dest.name}")
    try:
        tmp.write_text(FrameTimes(times=times).model_dump_json(), encoding="utf-8")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def run_prepare(
    video: str,
    assets_root: str,
    job_id: str | None = None,  # noqa: ARG001
) -> PreparedFiles:
    path = _stored_video(video, Path(assets_root))
    files = _prepared_files(path)
    proxy, strip = files.proxy, files.strip
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
        if not files.frames.is_file():
            _write_frame_times(proxy, files.frames)
    except _FFMPEG_ERRORS as exc:
        logger.warning("could not prepare %s", path, exc_info=True)
        raise EditRefusedError(_why_not_prepared(path, exc)) from exc
    return PreparedFiles(
        proxy=str(files.proxy),
        strip=str(files.strip),
        frames=str(files.frames),
    )


def _why_not_prepared(path: Path, exc: BaseException) -> str:
    if "timed out" in str(exc):
        return _UNREADABLE
    try:
        _seconds(path)
    except EditRefusedError:  # silent: unreadable is the answer
        return _UNREADABLE
    return _NOT_PREPARED


def _picture(framing: Framing) -> str | None:
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
    return threading.BoundedSemaphore(settings.web.edit_frame_parallel)


def frame(video: str, at: float, framing: Framing, assets_root: Path) -> FrameGrab:
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


def queue_cut(
    clips: list[Clip],
    framing: Framing,
    speed: float,
    assets_root: Path,
    actor: str,
    run: Callable[..., Any],
) -> str:
    paths = [_stored_video(clip.video, assets_root) for clip in clips]
    fitted = checked(
        [
            c.model_copy(update={"video": str(p)})
            for c, p in zip(clips, paths, strict=True)
        ],
        [_seconds(path) for path in paths],
    )
    if list_jobs_page({TASK}, 0, 1, actor, status="running").total:
        msg = "Your last cut is still running: wait for it"
        raise EditRefusedError(msg)
    first = fitted[0]
    params: dict[str, Any] = {
        "video": first.video,
        "start": first.start,
        "end": first.end,
        "turn": framing.turn,
        "mirror": framing.mirror,
        "aspect": framing.aspect.value,
        "size": framing.size.value,
        "scale": framing.scale,
        "x": framing.x,
        "y": framing.y,
        "speed": speed,
    }
    if len(fitted) > 1:
        params["clips"] = [clip.model_dump() for clip in fitted]
    return start_job(
        TASK,
        params,
        run,
        {**params, "assets_root": str(assets_root)},
        actor=actor,
        queue=QueueName.EDIT,
        job_timeout=cut_timeout_s(params),
        at_front=True,
    )


def cut_timeout_s(params: dict[str, Any]) -> int:
    clips = len(params.get("clips") or [params])
    return settings.web.edit_cut_timeout_s * clips + 60


def _make_cut(
    path: Path, start: float, end: float, framing: Framing, speed: float, root: Path
) -> Path:
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


def _make_join(clips: list[Clip], framing: Framing, speed: float, root: Path) -> Path:
    web = settings.web
    limit = web.edit_cut_timeout_s
    paths = [_stored_video(clip.video, root) for clip in clips]
    work = Path(settings.infra.uploads_root) / STAGING
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        fps = min(_fps(paths[0]) or _JOIN_FPS, _JOIN_MAX_FPS)
        frame: Size | None = None
        parts: list[Path] = []
        for index, (clip, path) in enumerate(zip(clips, paths, strict=True)):
            dest = Path(tmp) / f"part{index:02d}.mp4"
            trim.part(
                path,
                clip.start,
                clip.end,
                dest,
                short_side=framing.size.short_side,
                frame=frame,
                fps=fps,
                sound=has_audio(path, limit),
                threads=web.edit_threads,
                timeout_s=limit,
                picture=_picture(framing),
                speed=speed,
            )
            frame = frame or video_size(dest, limit)
            parts.append(dest)
        made = Path(tmp) / f"{Path(_shown_name(paths[0])).stem}_cut.mp4"
        trim.join(parts, made, threads=web.edit_threads, timeout_s=limit)
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
    job_id: str | None = None,  # noqa: ARG001
    clips: list[dict[str, Any]] | None = None,
) -> EditVideo:
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
        if clips:
            joined = [Clip.model_validate(clip) for clip in clips]
            stored = _make_join(joined, framing, speed, root)
        else:
            stored = _make_cut(path, start, end, framing, speed, root)
        _touch(stored)
        return _video(stored, _seconds(stored))
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
    return CutState(
        status="running", ahead=JobQueue(name=QueueName.EDIT).line().ahead(job_id)
    )
