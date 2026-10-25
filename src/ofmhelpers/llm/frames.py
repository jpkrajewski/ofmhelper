"""A whole video as one image: stills from its first frame to its last,
evenly spaced, tiled in time order (left to right, top to bottom), each
labelled with its time ("0:07.5"), so a model shown one picture can say
where something goes wrong. Motion between stills is lost; a glitch
lasting a moment may fall between two.

The stills span the video stream, not the file: audio running past the
picture would plan stills after its last frame. An HDR stream (HLG, as the
iPhone export writes, or PQ) is turned into BT.709 SDR before each still is
scaled: shown as if SDR it reads dim and washed out (about 40% less
saturated), not as the reel looks on a phone."""

import logging
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict

from ofmhelpers.log import get_logger
from ofmhelpers.utils.ffmpeg import binary, run

logger: logging.Logger = get_logger(__name__)

# The last still is taken this far before the end: a seek to the very end
# can decode no frame at all.
_END_MARGIN_S = 0.1
_LABEL_PAD = 4
# HDR transfers (ffprobe `color_transfer`): HLG and PQ (HDR10).
_HDR = frozenset({"arib-std-b67", "smpte2084"})
# SDR white inside an HDR picture, nits (ITU-R BT.2408): what the iPhone
# export maps SDR white to (iphones recorder `_video_filter`), so going back
# with it is that export's exact inverse; brighter highlights clip.
_SDR_WHITE_NITS = 203


class _Stream(BaseModel):
    """ffprobe's first video stream: its length and colour tags (each
    missing when unknown)."""

    model_config = ConfigDict(extra="ignore")

    duration: float | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None


class _Format(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: float | None = None


class _Probe(BaseModel):
    """`ffprobe -of json` of a video's first video stream and its file."""

    model_config = ConfigDict(extra="ignore")

    streams: list[_Stream] = []
    format: _Format = _Format()

    @property
    def stream(self) -> _Stream:
        return self.streams[0] if self.streams else _Stream()

    @property
    def seconds(self) -> float:
        """The picture's length: the video stream's, else the file's (some
        containers, webm/mkv, give a stream none)."""
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
    """How long `video`'s picture lasts (its first video stream; the file's
    duration when the stream gives none) -- the span stills are taken from.
    Raises `RuntimeError`/`OSError`/`ValueError`/`subprocess.SubprocessError`
    when ffprobe cannot read it."""
    return _probed(video, timeout_s).seconds


def _to_sdr(stream: _Stream) -> str:
    """The filters (ending in ",") turning `stream`'s HDR picture into
    BT.709 SDR, "" for SDR: zscale from its transfer, primaries and matrix
    (BT.2020 when untagged) to BT.709, SDR white at `_SDR_WHITE_NITS`."""
    if stream.color_transfer not in _HDR:
        return ""
    return (
        f"zscale=tin={stream.color_transfer}"
        f":pin={stream.color_primaries or 'bt2020'}"
        f":min={stream.color_space or 'bt2020nc'}"
        f":t=bt709:p=bt709:m=bt709:npl={_SDR_WHITE_NITS},format=yuv420p,"
    )


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
    """`frames` labelled stills of the whole `video` (its video stream, SDR),
    each `frame_px` wide, tiled into the JPEG `dest`. Raises `RuntimeError`/
    `OSError`/`ValueError`/`subprocess.SubprocessError` when ffmpeg cannot
    read the video."""
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
