import math
import tempfile
from pathlib import Path
lazy import logging

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict

from ofmhelpers.log import get_logger
from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.hdr import sdr_filters

logger: logging.Logger = get_logger(__name__)

_END_MARGIN_S = 0.1
_LABEL_PAD = 4


class _Stream(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None


class _Format(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: float | None = None


class _Probe(BaseModel):
    model_config = ConfigDict(extra="ignore")

    streams: list[_Stream] = []
    format: _Format = _Format()

    @property
    def stream(self) -> _Stream:
        return self.streams[0] if self.streams else _Stream()

    @property
    def seconds(self) -> float:
        found = self.stream.duration or self.format.duration
        if not found:
            msg = "ffprobe reports no duration"
            raise ValueError(msg)
        return found


def _probed(video: Path, timeout_s: int) -> _Probe:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "stream=duration,color_transfer,color_primaries,color_space"
                ":format=duration"
            ),
            "-of",
            "json",
            str(video),
        ],
        timeout_s,
    )
    return _Probe.model_validate_json(out)


def video_seconds(video: Path, timeout_s: int) -> float:
    return _probed(video, timeout_s).seconds


def _to_sdr(stream: _Stream) -> str:
    return sdr_filters(
        stream.color_transfer, stream.color_primaries, stream.color_space
    )


def still_times(seconds: float, count: int) -> list[float]:
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
    probe = _probed(video, timeout_s)
    colour = _to_sdr(probe.stream)
    stills: list[tuple[float, Image.Image]] = []
    with tempfile.TemporaryDirectory(prefix="sheet-") as work:
        for i, at in enumerate(still_times(probe.seconds, frames)):
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
                    f"{colour}scale={frame_px}:-2",
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
