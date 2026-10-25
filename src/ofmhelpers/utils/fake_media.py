"""Real, tiny media files for the fakes (OFM_FAKE_EXTERNALS): a playable
mp4 a browser can start, and still images. Made once per process; the mp4
by ffmpeg (in every image), cached under `settings.infra.fakes_dir` when set
so the app and the test seeding it serve the same bytes."""

from __future__ import annotations

import io
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.utils.ffmpeg import binary, run

MP4_SECONDS = 2
# The first callers may be several threads (a reel download pool) at once.
_MAKING = threading.Lock()


@lru_cache(maxsize=1)
def mp4() -> bytes:
    """A 2 s, 320x568 H.264 + AAC clip (a reel's shape)."""
    folder = Path(settings.infra.fakes_dir or tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "fake-reel.mp4"
    with _MAKING:
        if not path.exists():
            _make_mp4(folder, path)
    return path.read_bytes()


def _make_mp4(folder: Path, path: Path) -> None:
    # A name of its own: another process (the app, the test) may be first too.
    fd, name = tempfile.mkstemp(dir=folder, suffix=".part.mp4")
    os.close(fd)
    part = Path(name)
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x568:rate=25:duration={MP4_SECONDS}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={MP4_SECONDS}",
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


@lru_cache(maxsize=16)
def image(
    color: str = "teal", size: tuple[int, int] = (320, 568), fmt: str = "JPEG"
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()
