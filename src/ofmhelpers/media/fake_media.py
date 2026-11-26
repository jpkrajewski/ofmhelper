# Copyright (C) 2026 jpkrajewski

import io
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.media.ffmpeg import binary, run

MP4_SECONDS = 2
_MAKING = threading.Lock()


@lru_cache(maxsize=1)
def mp4() -> bytes:
    folder = Path(settings.infra.fakes_dir or tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "fake-reel.mp4"
    with _MAKING:
        if not path.exists():
            _make_mp4(folder, path)
    return path.read_bytes()


def _make_mp4(folder: Path, path: Path) -> None:
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
    color: str = "teal",
    size: tuple[int, int] = (320, 568),
    fmt: str = "JPEG",
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()
