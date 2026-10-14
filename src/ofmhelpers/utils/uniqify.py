"""One source video re-encoded into N subtly different variants.

Each copy gets its own speed / zoom / colour / grain / mirror / head-trim /
volume jitter, drawn from the ranges of a Strength preset, so no two outputs
are byte- or frame-identical. Ported from the standalone `captions/` uniqify
tool; the encoder settings match it (h264 High CRF 23, 30 fps, <=720 px wide,
aac 192k 44.1 kHz stereo, +faststart).
"""

import datetime
import json
import random
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

_CRF = 23
_FPS = 30
_MAX_WIDTH = 720
_AUDIO_BITRATE = "192k"
_AUDIO_RATE = 44100
_AUDIO_CHANNELS = 2
# "Device" metadata backdates creation_time by up to this much, so a batch
# doesn't all carry the second it was rendered.
_DEVICE_TIME_SPREAD = datetime.timedelta(days=7)

Range = tuple[float, float]


class Strength(StrEnum):
    MINIMAL = "minimal"
    MEDIUM = "medium"
    HARD = "hard"
    EXTREME = "extreme"


class MetadataMode(StrEnum):
    """strip: no container/stream tags at all. device: tags shaped like a
    phone capture (QuickTime brand, Core Media handler, recent creation_time)."""

    STRIP = "strip"
    DEVICE = "device"


# Every preset's jitter, scaled away from "unchanged" by this factor: 1.02
# makes each range 2% wider than the original tool's.
_STRENGTH_BOOST = 1.02

# The value of each knob that leaves the video unchanged.
_NEUTRAL = {
    "speed": 1.0,
    "saturation": 1.0,
    "contrast": 1.0,
    "brightness": 0.0,
    "zoom": 1.0,
    "noise": 0.0,
    "volume": 1.0,
}


class StrengthPreset(BaseModel, frozen=True):
    """Per-variant (min, max) sample ranges."""

    speed: Range
    saturation: Range
    contrast: Range
    brightness: Range
    zoom: Range
    noise: Range
    volume: Range

    def boosted(self, factor: float) -> "StrengthPreset":
        """Every range pushed `factor` times as far from neutral."""
        return StrengthPreset.model_validate(
            {
                name: tuple(
                    neutral + (v - neutral) * factor for v in getattr(self, name)
                )
                for name, neutral in _NEUTRAL.items()
            }
        )


_BASE_PRESETS: dict[Strength, StrengthPreset] = {
    Strength.MINIMAL: StrengthPreset(
        speed=(0.99, 1.01),
        saturation=(0.98, 1.03),
        contrast=(0.99, 1.02),
        brightness=(-0.01, 0.01),
        zoom=(1.0, 1.0015),
        noise=(0.0, 2.0),
        volume=(0.99, 1.01),
    ),
    Strength.MEDIUM: StrengthPreset(
        speed=(0.97, 1.03),
        saturation=(0.95, 1.08),
        contrast=(0.97, 1.05),
        brightness=(-0.02, 0.02),
        zoom=(1.0, 1.0022),
        noise=(0.0, 5.0),
        volume=(0.97, 1.03),
    ),
    Strength.HARD: StrengthPreset(
        speed=(0.94, 1.06),
        saturation=(0.92, 1.12),
        contrast=(0.94, 1.08),
        brightness=(-0.03, 0.03),
        zoom=(1.0, 1.0028),
        noise=(0.0, 9.0),
        volume=(0.94, 1.06),
    ),
    Strength.EXTREME: StrengthPreset(
        speed=(0.90, 1.10),
        saturation=(0.88, 1.18),
        contrast=(0.90, 1.12),
        brightness=(-0.05, 0.05),
        zoom=(1.0, 1.0035),
        noise=(0.0, 14.0),
        volume=(0.90, 1.10),
    ),
}

PRESETS = {s: p.boosted(_STRENGTH_BOOST) for s, p in _BASE_PRESETS.items()}


class VariantParams(BaseModel):
    """The jitter one copy was encoded with."""

    speed: float
    saturation: float
    contrast: float
    brightness: float
    zoom: float
    noise: float
    volume: float
    trim_head: float
    mirror: bool

    @classmethod
    def sample(
        cls,
        preset: StrengthPreset,
        rng: random.Random,
        mirror_pct: float,
        trim_secs: float,
    ) -> "VariantParams":
        return cls(
            speed=round(rng.uniform(*preset.speed), 4),
            saturation=round(rng.uniform(*preset.saturation), 3),
            contrast=round(rng.uniform(*preset.contrast), 3),
            brightness=round(rng.uniform(*preset.brightness), 3),
            zoom=round(rng.uniform(*preset.zoom), 4),
            noise=round(rng.uniform(*preset.noise), 1),
            volume=round(rng.uniform(*preset.volume), 3),
            trim_head=round(rng.uniform(0.0, trim_secs), 3),
            mirror=rng.random() * 100.0 < mirror_pct,
        )

    def video_filter(self) -> str:
        eq = (
            f"eq=saturation={self.saturation}:contrast={self.contrast}"
            f":brightness={self.brightness}"
        )
        vf = [eq]
        if self.zoom > 1.0:
            vf.append(f"crop=trunc(iw/{self.zoom}/2)*2:trunc(ih/{self.zoom}/2)*2")
            vf.append(f"scale=trunc(iw*{self.zoom}/2)*2:trunc(ih*{self.zoom}/2)*2")
        if self.mirror:
            vf.append("hflip")
        if self.noise > 0:
            vf.append(f"noise=alls={self.noise}:allf=t")
        vf.append(f"setpts=PTS/{self.speed}")
        # Cap the width and force even dimensions -- x264 rejects odd ones.
        vf.append(f"scale='min({_MAX_WIDTH},trunc(iw/2)*2)':-2")
        return ",".join(vf)

    def audio_filter(self) -> str:
        af = f"atempo={self.speed}"
        if self.volume != 1.0:
            af += f",volume={self.volume}"
        return af


def _binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        msg = f"{name} not available"
        raise FileNotFoundError(msg)
    return path


def _run(args: list[str], timeout_s: int) -> str:
    """Run ffmpeg/ffprobe; a failure raises with ffmpeg's own last lines --
    that is what the job's error shows, not the whole argv."""
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=timeout_s
        ).stdout
    except subprocess.CalledProcessError as exc:
        tail = " / ".join((exc.stderr or "").strip().splitlines()[-3:])
        msg = f"{Path(args[0]).name} failed: {tail or exc.returncode}"
        raise RuntimeError(msg) from exc


def has_audio(src: Path, timeout_s: int) -> bool:
    out = _run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(src),
        ],
        timeout_s,
    )
    return bool(json.loads(out or "{}").get("streams"))


def _device_metadata_args(now: datetime.datetime, rng: random.Random) -> list[str]:
    taken = now - rng.random() * _DEVICE_TIME_SPREAD
    iso = taken.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    return [
        "-metadata",
        "major_brand=qt  ",
        "-metadata",
        "minor_version=512",
        "-metadata",
        "compatible_brands=qt  ",
        "-metadata",
        f"creation_time={iso}",
        "-metadata:s:v:0",
        f"creation_time={iso}",
        "-metadata:s:v:0",
        "handler_name=Core Media Video",
        "-metadata:s:v:0",
        "vendor_id=FFMP",
        "-metadata:s:v:0",
        "encoder=H.264",
    ]


def encode_variant(
    src: Path,
    dst: Path,
    params: VariantParams,
    *,
    audio: bool,
    metadata: MetadataMode,
    rng: random.Random,
    timeout_s: int,
) -> None:
    args = [_binary("ffmpeg"), "-y", "-loglevel", "error"]
    if params.trim_head > 0:
        args += ["-ss", str(params.trim_head)]
    args += ["-i", str(src), "-r", str(_FPS), "-vf", params.video_filter()]
    if audio:
        args += [
            "-filter:a",
            params.audio_filter(),
            "-c:a",
            "aac",
            "-b:a",
            _AUDIO_BITRATE,
            "-ar",
            str(_AUDIO_RATE),
            "-ac",
            str(_AUDIO_CHANNELS),
        ]
    else:
        args += ["-an"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-profile:v",
        "high",
        "-crf",
        str(_CRF),
        "-pix_fmt",
        "yuv420p",
        # Never carry the source's own tags over, whichever mode.
        "-map_metadata",
        "-1",
    ]
    if metadata is MetadataMode.DEVICE:
        now = datetime.datetime.now(datetime.UTC)
        args += _device_metadata_args(now, rng)
        args += ["-movflags", "+faststart+use_metadata_tags"]
    else:
        args += ["-movflags", "+faststart"]
    args.append(str(dst))
    _run(args, timeout_s)


def generate_variants(
    src: Path,
    out_dir: Path,
    stem: str,
    copies: int,
    strength: Strength,
    mirror_pct: float,
    trim_secs: float,
    metadata: MetadataMode,
    timeout_s: int,
    seed: int | None = None,
) -> list[Path]:
    """Encode `copies` variants of `src` into `out_dir` as
    `<stem>_001.mp4`, `<stem>_002.mp4`, ... and return their paths."""
    rng = random.Random(seed)  # noqa: S311 - jitter, not cryptography
    preset = PRESETS[strength]
    audio = has_audio(src, timeout_s)
    outputs = []
    for i in range(1, copies + 1):
        params = VariantParams.sample(preset, rng, mirror_pct, trim_secs)
        dst = out_dir / f"{stem}_{i:03d}.mp4"
        encode_variant(
            src,
            dst,
            params,
            audio=audio,
            metadata=metadata,
            rng=rng,
            timeout_s=timeout_s,
        )
        outputs.append(dst)
    return outputs
