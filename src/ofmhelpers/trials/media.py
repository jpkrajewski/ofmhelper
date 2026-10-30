"""What the /trials page plays: the copy itself is the iPhone's 10-bit HLG
HEVC (hvc1, BT.2020), which Chrome on Windows shows as a black box -- and a
player that ignores HLG shows washed out (saturation -27% on a real copy,
2026-10-30). So the page gets an H.264 SDR preview and a first-frame poster
(`web.previews` writers), converted back from HLG the way the copy was made
(`zscale` at the profile's SDR white: a lossless round trip, measured). They
sit next to the copy and are made again once the copy is newer (a caption
applied). Raise one of `web.previews.PREVIEW_ERRORS`."""

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.iphones import load_profile
from ofmhelpers.log import get_logger
from ofmhelpers.repurpose.devices import load_device
from ofmhelpers.web.previews import (
    PREVIEW_ERRORS,
    write_atomic,
    write_video_preview,
    write_video_thumb,
)

logger = get_logger(__name__)

# What the AI watches: a 4K reel (28 Mb/s, ~50 MB) went through Gemini's
# Files API upload + poll; this goes inline. Gemini samples 1 frame a
# second, so 10 fps loses nothing; the sound stays for the transcript.
_AI_HEIGHT = 640
_AI_FPS = 10
# The page is where a copy is judged: 1080p, not the gallery's 720p.
_PREVIEW_HEIGHT = 1920
_PREVIEW_MAXRATE = "8M"

# The pipeline's device (snapchat_iphone16_usa.json): its recorder profile
# says how the copy was coloured.
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
    dest = copy.with_suffix(f".preview{_PREVIEW_HEIGHT}.mp4")
    if not _fresh(dest, copy):
        write_video_preview(
            copy,
            dest,
            pre_filter=_to_sdr(),
            height=_PREVIEW_HEIGHT,
            maxrate=_PREVIEW_MAXRATE,
        )
    return dest


def poster(copy: Path) -> Path:
    dest = copy.with_suffix(".poster.webp")
    if not _fresh(dest, copy):
        write_video_thumb(copy, dest, settings.web.poster_size, pre_filter=_to_sdr())
    return dest


def ai_proxy(src: Path) -> Path:
    """A small copy of the downloaded reel for the AI to watch, made once;
    the reel itself when it cannot be made."""
    dest = src.with_suffix(".ai.mp4")
    if _fresh(dest, src):
        return dest

    def write(tmp: Path) -> None:
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
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
            check=True,
            capture_output=True,
            timeout=settings.web.preview_timeout_s,
        )

    try:
        return write_atomic(dest, write)
    except PREVIEW_ERRORS:
        logger.warning("trials: AI proxy of %s failed", src, exc_info=True)
        return src
