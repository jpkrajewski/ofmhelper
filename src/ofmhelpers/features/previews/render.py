"""Small stand-ins for big media files, for pages that only need to show them.

A /clone result is a 4K export at ~28 Mbit/s (30-40 MB for 10 s) and a
/generate image a 6.5 MB PNG: a slow connection never got far enough into a
gallery of them to show anything (prod, 2026-10-21). Galleries show these
instead -- Download still serves the original:

- a result video: a 720p, bitrate-capped, faststart mp4 (`write_video_preview`)
  behind its first frame (`write_video_thumb`);
- a result image: a webp of at most `image_preview_px` (`write_image_thumb`).

Made for each job as it finishes (`warm_previews`, queued by `run_job` on the
previews queue), for older jobs by the startup backfill
(`features/previews/backfill.py`), and on request by the routes if neither got there
first. Every file is written via a temp file and renamed, so a killed or
timed-out ffmpeg never leaves a truncated file that later requests take for a
finished one.

A preview that could not be made leaves a marker (`{job}_{index}.failed`:
the source's size and mtime, how many tries failed). After `_FAILS_TO_BLOCK`
failures of that same file it is not tried again for `_RETRY_FAILED_S`, its
preview or its poster, by the backfill or a request, and the gallery shows the
original. A 61 MB clip timed out (180 s) on every deploy's backfill, 10 times in
4 days, each holding the only preview worker (prod 2026-11-04..08). One failure
alone (a busy CPU) is tried again; a replaced source at once.
"""

import hashlib
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.log import get_logger
from ofmhelpers.media import ffmpeg

logger = get_logger(__name__)


def previews_dir() -> Path:
    """Previews and posters of job results, keyed by job id + file index
    (what /files/{job}/{index} serves). Read per call, like every setting:
    a root fixed at import would outlive the test suite's own."""
    return Path(settings.infra.uploads_root) / ".previews"


# What a preview can fail with: a missing or broken ffmpeg, an unreadable
# source, a timeout (`utils.ffmpeg` raises RuntimeError for the last two).
PREVIEW_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
# A preview that failed this often is tried again only after this (a full
# disk passes).
_FAILS_TO_BLOCK = 2
_RETRY_FAILED_S = 7 * 24 * 60 * 60


class FailedBeforeError(RuntimeError):
    """This very file's preview failed too often lately: not tried again yet."""


def write_atomic(dest: Path, write: Callable[[Path], None]) -> Path:
    """`write` into a temp file next to `dest`, then rename it into place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=dest.parent, suffix=dest.suffix, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write(tmp_path)
        tmp_path.replace(dest)
    finally:
        tmp_path.unlink(missing_ok=True)
    return dest


def write_image_thumb(source: Path, dest: Path, size: int) -> None:
    with Image.open(source) as img:
        img.thumbnail((size, size))
        img.convert("RGB").save(dest, "WEBP")


def write_video_thumb(
    source: Path, dest: Path, size: int, *, pre_filter: str = "", at_s: float = 0
) -> None:
    """The frame at `at_s` (the first), scaled to fit a `size` box, as webp;
    `pre_filter` (ending in a comma) runs first.

    Extracted with ffmpeg rather than served as a <video preload="metadata">
    tile: that made the browser range-request every clip in a grid just to
    paint a poster frame. A cached frame is a few KB instead.

    `force_original_aspect_ratio` keeps a 9:16 reel from being stretched.
    """

    def write(tmp: Path) -> None:
        ffmpeg.run_bytes(
            [
                ffmpeg.binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-ss",
                f"{at_s:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"{pre_filter}scale={size}:{size}:force_original_aspect_ratio=decrease",
                "-f",
                "webp",
                str(tmp),
            ],
            settings.web.ffmpeg_timeout_s,
        )

    write_atomic(dest, write)


def write_video_preview(
    source: Path,
    dest: Path,
    *,
    pre_filter: str = "",
    height: int | None = None,
    maxrate: str | None = None,
) -> None:
    """At most `preview_height` lines (never upscaled), bitrate-capped H.264
    with the index up front (`+faststart`), so playback starts after the
    first few hundred KB instead of after the whole file. Two threads: the
    preview worker shares the machine with the app and the generations.
    `pre_filter` (ending in a comma) runs before the scale; `height` /
    `maxrate` override the gallery's settings (a page judging quality)."""
    cfg = settings.web
    height = height or cfg.preview_height
    maxrate = maxrate or cfg.preview_maxrate

    def write(tmp: Path) -> None:
        ffmpeg.run_bytes(
            [
                ffmpeg.binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"{pre_filter}scale=-2:'min({height},ih)'",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-maxrate",
                maxrate,
                "-bufsize",
                maxrate,
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(tmp),
            ],
            cfg.preview_timeout_s,
        )

    write_atomic(dest, write)


def preview_path(job_id: str, index: int, kind: str) -> Path:
    suffix = ".mp4" if kind == "video" else ".webp"
    return previews_dir() / f"{job_id}_{index}{suffix}"


def poster_path(job_id: str, index: int) -> Path:
    return previews_dir() / f"{job_id}_{index}_{settings.web.poster_size}.webp"


def failed_path(job_id: str, index: int) -> Path:
    return previews_dir() / f"{job_id}_{index}.failed"


def _stamp(source: Path) -> str:
    st = source.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


@dataclass(frozen=True)
class ResultPreview:
    """One result file of a job (`source`, its `index` in the job's results)
    and what a gallery shows of it."""

    source: Path
    job_id: str
    index: int

    def _failures(self) -> int:
        """How many tries at this file (same size and mtime) failed lately."""
        marker = failed_path(self.job_id, self.index)
        try:
            if marker.stat().st_mtime <= time.time() - _RETRY_FAILED_S:
                return 0
            stamp, _, count = marker.read_text(encoding="utf-8").rpartition(":")
            return int(count) if stamp == _stamp(self.source) else 0
        except OSError, ValueError:
            return 0

    def failed_before(self) -> bool:
        """Whether this file failed to preview too often lately to try again."""
        return self._failures() >= _FAILS_TO_BLOCK

    def mark_failed(self) -> None:
        try:
            count = self._failures() + 1
            marker = failed_path(self.job_id, self.index)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{_stamp(self.source)}:{count}", encoding="utf-8")
        except OSError:
            logger.warning(
                "preview failure of %s not marked", self.source, exc_info=True
            )

    def make(self) -> Path:
        """The result's preview, made unless it already exists. Raises one of
        PREVIEW_ERRORS when it can't be made, at once when this file failed
        lately (`failed_before`)."""
        source = self.source
        kind = classify_kind(source.name)
        dest = preview_path(self.job_id, self.index, kind)
        if dest.is_file():
            return dest
        if self.failed_before():
            msg = f"preview of {source.name} failed lately"
            raise FailedBeforeError(msg)
        try:
            if kind == "video":
                write_video_preview(source, dest)
            else:
                size = settings.web.image_preview_px
                write_atomic(dest, lambda tmp: write_image_thumb(source, tmp, size))
        except PREVIEW_ERRORS:
            self.mark_failed()
            raise
        return dest

    def poster(self) -> Path:
        """A result video's first frame, made unless it already exists."""
        dest = poster_path(self.job_id, self.index)
        if not dest.is_file():
            write_video_thumb(self.source, dest, settings.web.poster_size)
        return dest


def file_thumb(source: Path) -> Path:
    """Any stored image or video as a small webp, for the admin file manager
    (prod, 2026-10-22: a finished run with no card buttons could not even
    be told apart there). Keyed by path, size and mtime: a replaced file
    gets a new one. Raises one of PREVIEW_ERRORS when it can't be made."""
    st = source.stat()
    key = f"{source}:{st.st_size}:{st.st_mtime_ns}".encode()
    dest = previews_dir() / "files" / f"{hashlib.sha256(key).hexdigest()[:32]}.webp"
    if dest.is_file():
        return dest
    size = settings.web.poster_size
    if classify_kind(source.name) == "video":
        write_video_thumb(source, dest, size)
    else:
        write_atomic(dest, lambda tmp: write_image_thumb(source, tmp, size))
    return dest


def _media(sources: list[str | None]) -> list[tuple[int, Path, str]]:
    """(index, path, kind) of every result that has a picture to show and
    is still on disk."""
    found = []
    for index, raw in enumerate(sources):
        if raw is None:
            continue
        path = Path(raw)
        kind = classify_kind(path.name)
        if kind in ("image", "video") and path.is_file():
            found.append((index, path, kind))
    return found


def is_warm(job_id: str, sources: list[str | None]) -> bool:
    """Whether every preview (and video poster) of a job's results exists, a
    file that failed too often lately counting as done (its poster too)."""
    return all(
        ResultPreview(path, job_id, index).failed_before()
        or (
            preview_path(job_id, index, kind).is_file()
            and (kind != "video" or poster_path(job_id, index).is_file())
        )
        for index, path, kind in _media(sources)
    )


def warm_previews(job_id: str, sources: list[str | None]) -> None:
    """Make every preview a job's results need (`Job.local_paths()`), so the
    first viewer waits for none. One result that can't be previewed is
    logged and skipped; the route serves its original instead. One that
    failed too often lately is not tried again, preview or poster."""
    for index, path, kind in _media(sources):
        result = ResultPreview(path, job_id, index)
        if result.failed_before():
            continue
        try:
            result.make()
        except PREVIEW_ERRORS:
            logger.warning("could not preview %s", path, exc_info=True)
        if kind != "video":
            continue
        try:
            result.poster()
        except PREVIEW_ERRORS:
            result.mark_failed()
            logger.warning("could not make a poster of %s", path, exc_info=True)
