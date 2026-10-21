"""Real, tiny media files for the fakes (OFM_FAKE_EXTERNALS): a playable
mp4 a browser can start, and still images. Made once per process; the mp4
by ffmpeg (in every image), cached under `settings.infra.fakes_dir` when set
so the app and the test seeding it serve the same bytes."""

from __future__ import annotations

import io
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.utils.ffmpeg import binary, run

_VIDEO_S = 2


@lru_cache(maxsize=1)
def mp4() -> bytes:
    """A 2 s, 320x568 H.264 + AAC clip (a reel's shape)."""
    folder = Path(settings.infra.fakes_dir or tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "fake-reel.mp4"
    if not path.exists():
        # Per process: the app and a test may both be first.
        part = path.with_name(f"fake-reel.{os.getpid()}.part.mp4")
        run(
            [
                binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size=320x568:rate=25:duration={_VIDEO_S}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={_VIDEO_S}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
                str(part),
            ],
            timeout_s=60,
        )
        part.replace(path)
    return path.read_bytes()


@lru_cache(maxsize=16)
def image(
    color: str = "teal", size: tuple[int, int] = (320, 568), fmt: str = "JPEG"
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()
