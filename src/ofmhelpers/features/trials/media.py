from functools import lru_cache
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.features.previews.render import (
    PREVIEW_ERRORS,
    write_atomic,
    write_video_preview,
    write_video_thumb,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media import ffmpeg
from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.video_probe import duration_s

logger = get_logger(__name__)

_AI_HEIGHT = 640
_AI_FPS = 10
_PREVIEW_HEIGHT = 1920
_PREVIEW_MAXRATE = "8M"

_DEVICE = "iphone16_usa"


@lru_cache(maxsize=1)
def _to_sdr() -> str:
    recorder = load_device(_DEVICE).recorder
    if recorder is None:
        return ""
    v = load_profile(recorder.profile).video
    return (
        f"zscale=tin={v.color_transfer}:pin={v.color_primaries}:min={v.color_space}"
        f":t=bt709:p=bt709:m=bt709:npl={v.sdr_white_nits},format=yuv420p,"
    )


def _fresh(dest: Path, copy: Path) -> bool:
    return dest.is_file() and dest.stat().st_mtime_ns >= copy.stat().st_mtime_ns


def preview(copy: Path) -> Path:
    dest = preview_path(copy)
    if not _fresh(dest, copy):
        write_video_preview(
            copy,
            dest,
            pre_filter=_to_sdr(),
            height=_PREVIEW_HEIGHT,
            maxrate=_PREVIEW_MAXRATE,
        )
    return dest


def preview_path(copy: Path) -> Path:
    return copy.with_suffix(f".preview{_PREVIEW_HEIGHT}.mp4")


def poster_path(copy: Path) -> Path:
    return copy.with_suffix(".poster.webp")


def poster(copy: Path, *, again: bool = False) -> Path:
    dest = poster_path(copy)
    if again or not _fresh(dest, copy):
        middle = duration_s(copy, settings.web.ffmpeg_timeout_s) / 2
        write_video_thumb(
            copy, dest, settings.web.poster_size, pre_filter=_to_sdr(), at_s=middle
        )
    return dest


def ai_proxy(src: Path) -> Path:
    dest = src.with_suffix(".ai.mp4")
    if _fresh(dest, src):
        return dest

    def write(tmp: Path) -> None:
        ffmpeg.run_bytes(
            [
                ffmpeg.binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vf",
                f"scale=-2:'min({_AI_HEIGHT},ih)',fps={_AI_FPS},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "30",
                "-c:a",
                "aac",
                "-ac",
                "1",
                "-b:a",
                "64k",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(tmp),
            ],
            settings.web.preview_timeout_s,
        )

    try:
        return write_atomic(dest, write)
    except PREVIEW_ERRORS:
        logger.warning("trials: AI proxy of %s failed", src, exc_info=True)
        return src
