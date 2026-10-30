"""Device presets: how a copy is encoded and tagged, one JSON file each
(`<name>.json` here); a new device is a new file, no code.

- `strip` — legacy uniqify's encode (<=720 wide, 30 fps, x264 CRF 23 fast,
  AAC 192k 44.1 kHz), no tags. The default.
- `iphone_tags` — legacy uniqify's "device" metadata mode (QuickTime brand,
  Core Media handler, creation time within the last 7 days): tags only.
- `iphone17` — a real iPhone 17 Pro Max camera recording, by the
  `ofmhelpers.iphones` recorder (HEVC, the phone's moov, tag-for-tag with a
  real file). With any video/audio knobs set, they go into a near-lossless
  `master` first, then the recorder encodes that.
- `master` — near-lossless intermediate at the source's size and rate.
- `capcut` — `utils.capcut`'s CapCut 4K desktop export (2160 short side,
  59.94 fps, H.264 Main L5.1 at 28 Mb/s, CapCut's tags, `encoder` renamed in
  place, no x264 SEI, empty compressor name).
- `shuffled` — Duplyfi's per-copy encoder: random x264 preset, CRF, GOP and
  B-frames, a frame rate near the source's, random audio bitrate, no SEI.
"""

import random
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ofmhelpers.iphones.models import Place
from ofmhelpers.repurpose.probe import Source
from ofmhelpers.repurpose.values import Value, even

_HERE = Path(__file__).parent

# Frame rates a copy may take for fps "near_source": at or below the
# source's, at most 10% fewer frames, a different one when there is a choice
# (Duplyfi's `pickOutputFrameRate`).
_STANDARD_RATES = (
    ("24000/1001", 24000 / 1001),
    ("24", 24.0),
    ("25", 25.0),
    ("30000/1001", 30000 / 1001),
    ("30", 30.0),
    ("50", 50.0),
    ("60000/1001", 60000 / 1001),
    ("60", 60.0),
)
_MAX_DROP = 0.1
_SAME_RATE = 0.01
_NTSC_FAMILIES = ((30, ("30000/1001", "30")), (60, ("60000/1001", "60")))
_NTSC_TOLERANCE = 0.5
_FALLBACK_FPS = "30"


class VideoEncode(BaseModel, frozen=True):
    codec: str = "libx264"
    profile: str = "high"
    level: str | None = None
    # One x264 preset, or a list to pick from per copy.
    preset: list[str] | str = "fast"
    crf: Value | None = 23
    # Set: encode at this bitrate (with maxrate/bufsize) instead of a CRF.
    bitrate_kbps: Value | None = None
    maxrate_factor: float = 1.2
    bufsize_factor: float = 2.0
    gop: Value | None = None
    bframes: Value | None = None
    x264_params: str | None = None
    tag: str | None = None
    # x264 writes its version and settings as an SEI NAL; drop it.
    strip_sei: bool = False
    pix_fmt: str = "yuv420p"
    colorspace: str | None = None
    color_primaries: str | None = None
    color_trc: str | None = None
    color_range: str | None = None
    timescale: int | None = None
    # A number / "30000/1001", "source" (keep it) or "near_source" (Duplyfi).
    fps: float | str = 30
    # Output size: at most this wide, or this short side; neither = source.
    max_width: int | None = 720
    short_side: int | None = None
    scaler: str = "bicubic"

    def frame_rate(self, src: Source, rng: random.Random) -> str | None:
        """The -r value; None keeps the source's."""
        if self.fps == "source":
            return None
        if self.fps == "near_source":
            return _near_source(src.fps, rng)
        return str(self.fps)


class AudioEncode(BaseModel, frozen=True):
    codec: str = "aac"
    bitrate_kbps: Value = 192
    sample_rate: int = 44100
    channels: int = 2
    # No encoder version inside the AAC data.
    bitexact: bool = False


class Container(BaseModel, frozen=True):
    brand: str | None = None
    movflags: list[str] = ["+faststart"]
    # No ffmpeg version stamps on the file or the video stream.
    bitexact: bool = False


class Tags(BaseModel, frozen=True):
    """Metadata written; "{creation_time}" in a value is the copy's time."""

    format: dict[str, str] = {}
    video: dict[str, str] = {}
    audio: dict[str, str] = {}
    creation_time: Literal["now", "recent"] = "now"
    # "recent": up to this many days before now.
    recent_days: float = 7.0


class Patches(BaseModel, frozen=True):
    """Same-length byte patches after the mux (`repurpose.container`)."""

    # A placeholder mdta key renamed to the real one: ffmpeg can't write
    # some keys (`encoder`) itself.
    rename_keys: dict[str, str] = {}
    # Zero the video sample entry's compressorname ("Lavc libx264").
    blank_compressor_name: bool = False


class Recorder(BaseModel, frozen=True):
    """A camera recording by `ofmhelpers.iphones` instead of our encode."""

    # `iphones/<profile>/profile.json`.
    profile: str
    # The phone's time zone (IANA name); the recording ends now there.
    time_zone: str = "America/New_York"
    # Where it was filmed; None: Location Services off.
    place: Place | None = None


class Device(BaseModel, frozen=True):
    label: str
    video: VideoEncode = VideoEncode()
    audio: AudioEncode = AudioEncode()
    container: Container = Container()
    tags: Tags = Tags()
    patches: Patches = Patches()
    recorder: Recorder | None = None

    def output_size(self, src: Source) -> tuple[int, int]:
        """The source as played, fitted to the device's size rule, even."""
        w, h = src.display_size
        v = self.video
        if v.short_side:
            if w < h:
                return even(v.short_side), even(v.short_side * h / w)
            return even(v.short_side * w / h), even(v.short_side)
        if v.max_width:
            out_w = min(v.max_width, w) // 2 * 2
            return out_w, even(h * out_w / w)
        return even(w), even(h)


def _near_source(source_fps: float | None, rng: random.Random) -> str:
    if not source_fps:
        return _FALLBACK_FPS
    nearby = [
        (arg, fps)
        for arg, fps in _STANDARD_RATES
        if source_fps * (1 - _MAX_DROP) <= fps <= source_fps + _SAME_RATE
    ]
    if not nearby:
        return _FALLBACK_FPS
    for nominal, args in _NTSC_FAMILIES:
        if abs(source_fps - nominal) <= _NTSC_TOLERANCE:
            return rng.choice(args)
    changed = [arg for arg, fps in nearby if abs(fps - source_fps) > _SAME_RATE]
    return rng.choice(changed or [arg for arg, _ in nearby])


def device_names() -> list[str]:
    return sorted(p.stem for p in _HERE.glob("*.json"))


@lru_cache
def load_device(name_or_path: str) -> Device:
    """A device by name (`devices/<name>.json`) or a JSON file path."""
    path = Path(name_or_path)
    if path.suffix != ".json":
        path = _HERE / f"{name_or_path}.json"
    if not path.is_file():
        msg = f"unknown device {name_or_path!r}; known: {', '.join(device_names())}"
        raise ValueError(msg)
    return Device.model_validate_json(path.read_text(encoding="utf-8"))
