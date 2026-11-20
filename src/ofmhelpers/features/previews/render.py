import hashlib
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.log import get_logger
from ofmhelpers.media import ffmpeg

logger = get_logger(__name__)


def previews_dir() -> Path:
    return Path(settings.infra.uploads_root) / ".previews"


PREVIEW_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
_FAILS_TO_BLOCK = 2
_RETRY_FAILED_S = 7 * 24 * 60 * 60


class FailedBeforeError(RuntimeError): ...


def write_atomic(dest: Path, write: Callable[[Path], None]) -> Path:
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


class ResultPreview(BaseModel, frozen=True):
    source: Path
    job_id: str
    index: int

    def _failures(self) -> int:
        marker = failed_path(self.job_id, self.index)
        try:
            if marker.stat().st_mtime <= time.time() - _RETRY_FAILED_S:
                return 0
            stamp, _, count = marker.read_text(encoding="utf-8").rpartition(":")
            return int(count) if stamp == _stamp(self.source) else 0
        except FileNotFoundError:
            logger.debug("preview %s never failed", marker.name, exc_info=True)
            return 0
        except OSError, ValueError:
            logger.warning("preview marker %s unreadable", marker.name, exc_info=True)
            return 0

    def failed_before(self) -> bool:
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
        dest = poster_path(self.job_id, self.index)
        if not dest.is_file():
            write_video_thumb(self.source, dest, settings.web.poster_size)
        return dest


def file_thumb(source: Path) -> Path:
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
    return all(
        ResultPreview(source=path, job_id=job_id, index=index).failed_before()
        or (
            preview_path(job_id, index, kind).is_file()
            and (kind != "video" or poster_path(job_id, index).is_file())
        )
        for index, path, kind in _media(sources)
    )


def warm_previews(job_id: str, sources: list[str | None]) -> None:
    for index, path, kind in _media(sources):
        result = ResultPreview(source=path, job_id=job_id, index=index)
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
