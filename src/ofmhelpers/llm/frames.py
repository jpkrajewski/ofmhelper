"""A whole video as one image: stills from its first frame to its last,
evenly spaced, tiled in time order (left to right, top to bottom), each
labelled with its time ("0:07.5"), so a model shown one picture can say
where something goes wrong. Motion between stills is lost; a glitch
lasting a moment may fall between two."""

import logging
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ofmhelpers.log import get_logger
from ofmhelpers.utils.ffmpeg import binary, run
from ofmhelpers.utils.video_probe import duration_s

logger: logging.Logger = get_logger(__name__)

# The last still is taken this far before the end: a seek to the very end
# can decode no frame at all.
_END_MARGIN_S = 0.1
_LABEL_PAD = 4


def still_times(seconds: float, count: int) -> list[float]:
    """`count` moments spread from the start to the end of a video
    `seconds` long, both ends included."""
    if count <= 1:
        return [0.0]
    last = max(seconds - _END_MARGIN_S, 0.0)
    return [round(last * i / (count - 1), 3) for i in range(count)]


def time_label(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}:{rest:04.1f}"


def contact_sheet(
    video: Path, dest: Path, frames: int, frame_px: int, timeout_s: int
) -> Path:
    """`frames` labelled stills of the whole `video`, each `frame_px` wide,
    tiled into the JPEG `dest`. Raises `RuntimeError`/`OSError`/
    `subprocess.SubprocessError` when ffmpeg cannot read the video."""
    seconds = duration_s(video, timeout_s)
    stills: list[tuple[float, Image.Image]] = []
    with tempfile.TemporaryDirectory(prefix="sheet-") as work:
        for i, at in enumerate(still_times(seconds, frames)):
            out = Path(work) / f"{i:03d}.jpg"
            run(
                [
                    binary("ffmpeg"),
                    "-y",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={frame_px}:-2",
                    "-q:v",
                    "3",
                    str(out),
                ],
                timeout_s=timeout_s,
            )
            if not out.exists():
                logger.warning(
                    "contact sheet: no frame at %.2f s of %s", at, video.name
                )
                continue
            with Image.open(out) as still:
                stills.append((at, still.convert("RGB")))
    if not stills:
        msg = f"ffmpeg decoded no frame of {video.name}"
        raise RuntimeError(msg)
    _tiled(stills).save(dest, "JPEG", quality=88)
    return dest


def _tiled(stills: list[tuple[float, Image.Image]]) -> Image.Image:
    width, height = stills[0][1].size
    columns = math.ceil(math.sqrt(len(stills)))
    rows = math.ceil(len(stills) / columns)
    sheet = Image.new("RGB", (columns * width, rows * height), "black")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(12, width // 12))
    for i, (at, still) in enumerate(stills):
        x, y = (i % columns) * width, (i // columns) * height
        sheet.paste(still.resize((width, height)), (x, y))
        text = time_label(at)
        box = draw.textbbox((x + _LABEL_PAD, y + _LABEL_PAD), text, font=font)
        draw.rectangle(
            (
                box[0] - _LABEL_PAD,
                box[1] - _LABEL_PAD,
                box[2] + _LABEL_PAD,
                box[3] + _LABEL_PAD,
            ),
            fill="black",
        )
        draw.text((x + _LABEL_PAD, y + _LABEL_PAD), text, fill="white", font=font)
    return sheet
