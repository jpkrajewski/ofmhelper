# Copyright (C) 2026 jpkrajewski

import datetime as dt
import json
import mmap
import random
lazy from pathlib import Path

from pydantic import BaseModel, Field

from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.size import Size

Range = tuple[float, float]

_JITTER_SATURATION: Range = (0.949, 1.0816)
_JITTER_CONTRAST: Range = (0.9694, 1.051)
_JITTER_BRIGHTNESS: Range = (-0.0204, 0.0204)
_JITTER_ZOOM: Range = (1.0, 1.0022)
_JITTER_NOISE: Range = (0.0, 5.1)
_JITTER_VOLUME: Range = (0.9694, 1.0306)

_EXPOSURE_STOPS_PER_UNIT = 1 / 150
_CONTRAST_GAMMA_PER_UNIT = 1 / 200
_CONTRAST_SATURATION_PER_UNIT = 1 / 90
_SHARPEN_PER_UNIT = 1 / 50
_PARTICLES_GRAIN_PER_UNIT = 0.1

_FPS = "60000/1001"
_TIMESCALE = "60000"
_VIDEO_BITRATE = "28M"

_VIDEO_MAXRATE = "31M"
_VIDEO_BUFSIZE = "28M"
_X264_PRESET = "veryfast"
_X264_PARAMS = "bframes=0:colorprim=bt709:transfer=bt709:colormatrix=bt709"
_DROP_SEI = "filter_units=remove_types=6"
_AUDIO_BITRATE = "192k"
_AUDIO_RATE = "44100"
_AUDIO_CHANNELS = "2"
_SHORT_SIDE_4K = 2160


def _even(v: float) -> int:
    return int(v / 2) * 2


class SourceInfo(BaseModel, frozen=True):
    duration: float
    width: int
    height: int
    has_audio: bool

    @classmethod
    def from_ffprobe(cls, out: str) -> SourceInfo:
        probe = json.loads(out)
        streams = probe.get("streams", [])
        video = next(s for s in streams if s["codec_type"] == "video")
        return cls(
            duration=float(probe["format"]["duration"]),
            width=video["width"],
            height=video["height"],
            has_audio=any(s["codec_type"] == "audio" for s in streams),
        )

    def size_4k(self) -> Size:
        if self.width > self.height:
            return Size(
                width=_even(_SHORT_SIDE_4K * self.width / self.height),
                height=_SHORT_SIDE_4K,
            )
        return Size(
            width=_SHORT_SIDE_4K,
            height=_even(_SHORT_SIDE_4K * self.height / self.width),
        )


class Jitter(BaseModel, frozen=True):
    saturation: float
    contrast: float
    brightness: float
    zoom: float
    noise: float
    volume: float

    @classmethod
    def sample(cls, rng: random.Random) -> Jitter:
        return cls(
            saturation=round(rng.uniform(*_JITTER_SATURATION), 3),
            contrast=round(rng.uniform(*_JITTER_CONTRAST), 3),
            brightness=round(rng.uniform(*_JITTER_BRIGHTNESS), 3),
            zoom=round(rng.uniform(*_JITTER_ZOOM), 4),
            noise=round(rng.uniform(*_JITTER_NOISE), 1),
            volume=round(rng.uniform(*_JITTER_VOLUME), 3),
        )


class CapcutPreset(BaseModel, frozen=True):
    scale: float = Field(100.0, ge=100.0, le=400.0)
    move_down: float = Field(0.0, ge=-50.0, le=50.0)
    exposure: float = Field(0.0, ge=-50.0, le=50.0)
    contrast: float = Field(0.0, ge=-50.0, le=50.0)
    sharpen: float = Field(0.0, ge=0.0, le=100.0)
    particles: float = Field(0.0, ge=0.0, le=100.0)
    speed: float = Field(0.0, ge=-50.0, le=100.0)
    trim_head: float = Field(0.0, ge=0.0)
    trim_tail: float = Field(0.0, ge=0.0)
    mirror: bool = False

    @property
    def tempo(self) -> float:
        return 1.0 + self.speed / 100.0

    def crop(self, src: SourceInfo, zoom: float) -> str:
        z = self.scale / 100.0 * zoom
        w, h = _even(src.width / z), _even(src.height / z)
        x = _even((src.width - w) / 2)
        y = (src.height - h) / 2 - src.height * self.move_down / 100.0
        y = _even(min(max(y, 0.0), src.height - h))
        return f"crop={w}:{h}:{x}:{y}"

    def video_filter(self, src: SourceInfo, jitter: Jitter) -> str:
        out = src.size_4k()
        gamma = 1.0 + self.contrast * _CONTRAST_GAMMA_PER_UNIT
        saturation = (
            1.0 + self.contrast * _CONTRAST_SATURATION_PER_UNIT
        ) * jitter.saturation
        eq = (
            f"eq=contrast={jitter.contrast}:brightness={jitter.brightness}"
            f":saturation={saturation:.4f}:gamma={gamma:.4f}"
        )
        vf = ["hflip"] if self.mirror else []
        vf += [self.crop(src, jitter.zoom), eq]
        if jitter.noise:
            vf.append(f"noise=alls={jitter.noise:g}:allf=t")
        if self.exposure:
            stops = self.exposure * _EXPOSURE_STOPS_PER_UNIT
            vf.append(f"exposure=exposure={stops:.4f}")
        vf.append(f"scale={out.width}:{out.height}:flags=lanczos")
        if self.sharpen:
            vf.append(f"unsharp=5:5:{self.sharpen * _SHARPEN_PER_UNIT:.4f}")
        if self.particles:
            grain = self.particles * _PARTICLES_GRAIN_PER_UNIT
            vf.append(f"format=yuv420p,noise=alls={grain:g}:allf=t")
        vf.append(f"setpts=PTS/{self.tempo:g}")
        vf.append("format=yuv420p")
        return ",".join(vf)

    def audio_filter(self, jitter: Jitter) -> str:
        return f"atempo={self.tempo:g},volume={jitter.volume}"


DEFAULT_PRESET = CapcutPreset(
    scale=110,
    move_down=2,
    exposure=-3,
    contrast=5,
    sharpen=15,
    particles=10,
    speed=3,
    trim_head=0.3,
    trim_tail=0.3,
)


_CAPCUT_FORMAT_TAGS = {
    "Hw": "1",
    "bitrate": "55000000",
    "maxrate": "0",
    "te_is_reencode": "1",
}
_CAPCUT_HANDLERS = {"v": "VideoHandler", "a": "SoundHandler"}
_CAPCUT_ENCODER = "Lavf61.1.100"
_ENCODER_KEY = b"encoder"
_PLACEHOLDER_KEY = b"zzcodec"
_COMPRESSOR_NAME_OFFSET = 42
_COMPRESSOR_NAME_LEN = 32


def probe(src: Path, timeout_s: int) -> SourceInfo:
    out = run(
        [
            binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height:format=duration",
            "-of",
            "json",
            str(src),
        ],
        timeout_s,
    )
    return SourceInfo.from_ffprobe(out)


def capcut_metadata_args(now: dt.datetime) -> list[str]:
    iso = now.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    args = ["-map_metadata", "-1", "-fflags", "+bitexact"]
    args += ["-flags:v", "+bitexact", "-flags:a", "+bitexact"]
    args += ["-brand", "isom"]
    tags = {
        "creation_time": iso,
        **_CAPCUT_FORMAT_TAGS,
        _PLACEHOLDER_KEY.decode(): _CAPCUT_ENCODER,
    }
    for key, value in tags.items():
        args += ["-metadata", f"{key}={value}"]
    for stream, handler in _CAPCUT_HANDLERS.items():
        args += [
            f"-metadata:s:{stream}:0",
            f"handler_name={handler}",
            f"-metadata:s:{stream}:0",
            f"creation_time={iso}",
        ]
    return [*args, "-movflags", "+faststart+use_metadata_tags"]


def _encode_args(src: SourceInfo, preset: CapcutPreset, jitter: Jitter) -> list[str]:
    args = ["-vf", preset.video_filter(src, jitter)]
    if src.has_audio:
        args += ["-filter:a", preset.audio_filter(jitter)]
    return [
        *args,
        "-r",
        _FPS,
        "-c:v",
        "libx264",
        "-preset",
        _X264_PRESET,
        "-b:v",
        _VIDEO_BITRATE,
        "-maxrate:v",
        _VIDEO_MAXRATE,
        "-bufsize:v",
        _VIDEO_BUFSIZE,
        "-x264-params",
        _X264_PARAMS,
        "-profile:v",
        "main",
        "-level:v",
        "5.1",
        "-tag:v",
        "avc1",
        "-bsf:v",
        _DROP_SEI,
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-video_track_timescale",
        _TIMESCALE,
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        "-ar",
        _AUDIO_RATE,
        "-ac",
        _AUDIO_CHANNELS,
    ]


def apply_preset(
    src: Path,
    dst: Path,
    preset: CapcutPreset,
    timeout_s: int,
    seed: int | None = None,
) -> Jitter:
    info = probe(src, timeout_s)
    keep = info.duration - preset.trim_head - preset.trim_tail
    if keep <= 0:
        msg = f"{src.name} is {info.duration:.2f}s, shorter than the trims"
        raise ValueError(msg)
    jitter = Jitter.sample(random.Random(seed))  # noqa: S311 - not cryptography
    run(
        [
            binary("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{preset.trim_head:g}",
            "-t",
            f"{keep:.3f}",
            "-i",
            str(src),
            *_encode_args(info, preset, jitter),
            *capcut_metadata_args(dt.datetime.now(dt.UTC)),
            str(dst),
        ],
        timeout_s,
    )
    with dst.open("r+b") as f, mmap.mmap(f.fileno(), 0) as m:
        _rename_encoder_key(m)
        _clear_compressor_name(m)
    return jitter


def _find(m: mmap.mmap, needle: bytes, start: int = 0) -> int:
    at = m.find(needle, start)
    if at < 0:
        msg = f"{needle!r} not found in the encoded file"
        raise RuntimeError(msg)
    return at


def _rename_encoder_key(m: mmap.mmap) -> None:
    start = _find(m, b"mdta" + _PLACEHOLDER_KEY) + len(b"mdta")
    m[start : start + len(_ENCODER_KEY)] = _ENCODER_KEY


def _clear_compressor_name(m: mmap.mmap) -> None:
    entry = _find(m, b"avc1", _find(m, b"stsd"))
    start = entry + len(b"avc1") + _COMPRESSOR_NAME_OFFSET
    m[start : start + _COMPRESSOR_NAME_LEN] = bytes(_COMPRESSOR_NAME_LEN)
