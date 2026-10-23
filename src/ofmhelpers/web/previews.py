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
(`web/preview_backfill.py`), and on request by the routes if neither got there
first. Every file is written via a temp file and renamed, so a killed or
timed-out ffmpeg never leaves a truncated file that later requests take for a
finished one.
"""

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.media_kinds import classify_kind

logger = get_logger(__name__)


def previews_dir() -> Path:
    """Previews and posters of job results, keyed by job id + file index
    (what /files/{job}/{index} serves). Read per call, like every setting:
    a root fixed at import would outlive the test suite's own."""
    return Path(settings.infra.uploads_root) / ".previews"


# What a preview can fail with: a missing or broken ffmpeg, an unreadable
# source, a timeout.
PREVIEW_ERRORS = (OSError, ValueError, subprocess.SubprocessError)


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not available"
        raise FileNotFoundError(msg)
    return ffmpeg


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


def write_video_thumb(source: Path, dest: Path, size: int) -> None:
    """First frame, scaled to fit a `size` box, as webp.

    Extracted with ffmpeg rather than served as a <video preload="metadata">
    tile: that made the browser range-request every clip in a grid just to
    paint a poster frame. A cached frame is a few KB instead.

    `force_original_aspect_ratio` keeps a 9:16 reel from being stretched.
    """

    def write(tmp: Path) -> None:
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                "-f",
                "webp",
                str(tmp),
            ],
            check=True,
            capture_output=True,
            timeout=settings.web.ffmpeg_timeout_s,
        )

    write_atomic(dest, write)


def write_video_preview(source: Path, dest: Path) -> None:
    """At most `preview_height` lines (never upscaled), bitrate-capped H.264
    with the index up front (`+faststart`), so playback starts after the
    first few hundred KB instead of after the whole file. Two threads: the
    preview worker shares the machine with the app and the generations."""
    cfg = settings.web

    def write(tmp: Path) -> None:
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-vf",
                f"scale=-2:'min({cfg.preview_height},ih)'",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-maxrate",
                cfg.preview_maxrate,
                "-bufsize",
                cfg.preview_maxrate,
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
            check=True,
            capture_output=True,
            timeout=cfg.preview_timeout_s,
        )

    write_atomic(dest, write)


def preview_path(job_id: str, index: int, kind: str) -> Path:
    suffix = ".mp4" if kind == "video" else ".webp"
    return previews_dir() / f"{job_id}_{index}{suffix}"


def poster_path(job_id: str, index: int) -> Path:
    return previews_dir() / f"{job_id}_{index}_{settings.web.poster_size}.webp"


def make_preview(source: Path, job_id: str, index: int) -> Path:
    """A result's preview, made unless it already exists. Raises one of
    PREVIEW_ERRORS when it can't be made."""
    kind = classify_kind(source.name)
    dest = preview_path(job_id, index, kind)
    if dest.is_file():
        return dest
    if kind == "video":
        write_video_preview(source, dest)
    else:
        size = settings.web.image_preview_px
        write_atomic(dest, lambda tmp: write_image_thumb(source, tmp, size))
    return dest


def make_poster(source: Path, job_id: str, index: int) -> Path:
    """A result video's first frame, made unless it already exists."""
    dest = poster_path(job_id, index)
    if not dest.is_file():
        write_video_thumb(source, dest, settings.web.poster_size)
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
    """Whether every preview (and video poster) of a job's results exists."""
    return all(
        preview_path(job_id, index, kind).is_file()
        and (kind != "video" or poster_path(job_id, index).is_file())
        for index, _path, kind in _media(sources)
    )


def warm_previews(job_id: str, sources: list[str | None]) -> None:
    """Make every preview a job's results need (`Job.local_paths()`), so the
    first viewer waits for none. One result that can't be previewed is
    logged and skipped; the route serves its original instead."""
    for index, path, kind in _media(sources):
        try:
            make_preview(path, job_id, index)
            if kind == "video":
                make_poster(path, job_id, index)
        except PREVIEW_ERRORS:
            logger.warning("could not preview %s", path, exc_info=True)
