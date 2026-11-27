# Copyright (C) 2026 jpkrajewski

import math
from typing import Annotated, Self
lazy import random
lazy from pathlib import Path

from pydantic import BaseModel, Field, StringConstraints

from ofmhelpers.media.repurpose.extras.snapchat import render as render_snapchat
from ofmhelpers.media.repurpose.transformations.adjust import (
    AdjustDraws,
    AdjustSettings,
)
from ofmhelpers.media.repurpose.transformations.constants import (
    AdjustConstants,
    VideoConstants,
)
from ofmhelpers.media.repurpose.transformations.handheld import Handheld
from ofmhelpers.media.repurpose.transformations.patterns import Pattern
from ofmhelpers.media.repurpose.transformations.patterns import write as write_pattern
from ofmhelpers.media.repurpose.values import (
    Knobs,
    Value,
    chance,
    draw,
    draw_int,
    even,
    pick,
)
from ofmhelpers.media.size import Size
lazy from ofmhelpers.media.repurpose.probe import Source


class VideoSettings(Knobs, frozen=True):
    constants: VideoConstants = Field(default=VideoConstants(), exclude=True)
    trim_head_s: Value | None = None
    trim_tail_s: Value | None = None
    cut_head: Value | None = None
    cut_tail: Value | None = None
    speed: Value | None = None
    mirror: float | None = None
    rotate: Value | None = None
    rotate_both_ways: bool = False
    zoom: Value | None = None
    shift_y: Value | None = None
    corner_zoom: Value | None = None
    stretch: Value | None = None
    tilt: Value | None = None
    perspective: Value | None = None
    shake: Value | None = None
    drift: Value | None = None
    saturation: Value | None = None
    contrast: Value | None = None
    brightness: Value | None = None
    gamma: Value | None = None
    colortemp: Value | None = None
    tone_mid: Value | None = None
    tone_shoulder: Value | None = None
    tint: Value | None = None
    sharpen: Value | None = None
    black_and_white: float | None = None
    vignette: Value | None = None
    chroma_px: Value | None = None
    noise: Value | None = None
    noise_flags: Annotated[str, StringConstraints(pattern=r"^[aptu](\+[aptu])*$")] = "t"
    exposure: Value | None = None
    border: Value | None = None
    pattern: list[str] | str | None = None
    pattern_opacity: Value | None = None
    look: list[str] | None = None
    size: Value | None = None
    sharpen_late: Value | None = None
    particles: Value | None = None
    snapchat_caption: str | None = None
    adjust: AdjustSettings | None = None

    def merged(self, extra: VideoSettings | None) -> VideoSettings:
        if extra is None:
            return self
        return self.model_copy(update=extra.model_dump(exclude_unset=True))


class Timeline(BaseModel, frozen=True):
    start_s: float
    duration_s: float
    speed: float


class VideoDraws(BaseModel, frozen=True):
    timeline: Timeline
    output_size: tuple[int, int]
    mirror: bool
    rotate: float | None
    rotate_cover: float | None
    zoom: float | None
    shift_y: float | None
    corner: tuple[int, int] | None
    corner_zoom: float | None
    stretch: tuple[float, float] | None
    handheld: Handheld
    saturation: float | None
    contrast: float | None
    brightness: float | None
    gamma: float | None
    colortemp: int | None
    tone: tuple[int, int] | None
    tint: float | None
    sharpen: float | None
    black_and_white: bool
    vignette: float | None
    chroma: tuple[int, int] | None
    noise: float | None
    noise_seed: int
    exposure: float | None
    look: str
    border: float | None
    pattern: str | None
    pattern_seed: int
    pattern_opacity: float | None
    sharpen_late: float | None
    particles: float | None
    adjust: AdjustDraws | None


class _Cuts(BaseModel, frozen=True):
    head: float
    tail: float


class _Stretch(BaseModel, frozen=True):
    x: float
    y: float


class _Shift(BaseModel, frozen=True):
    x: int
    y: int


def _cuts(s: VideoSettings, src: Source, rng: random.Random) -> _Cuts:
    head = (draw(s.trim_head_s, rng) or 0.0) + (
        draw(s.cut_head, rng) or 0.0
    ) * src.duration
    tail = (draw(s.trim_tail_s, rng) or 0.0) + (
        draw(s.cut_tail, rng) or 0.0
    ) * src.duration
    limit = src.duration * (1 - s.constants.min_kept_share)
    if head + tail > limit:
        scale = limit / (head + tail)
        head, tail = head * scale, tail * scale
    return _Cuts(head=head, tail=tail)


def _timeline(s: VideoSettings, src: Source, rng: random.Random) -> Timeline:
    cuts = _cuts(s, src, rng)
    head, tail = cuts.head, cuts.tail
    return Timeline(
        start_s=round(head, 3),
        duration_s=round(src.duration - head - tail, 3),
        speed=round(draw(s.speed, rng) or 1.0, 4),
    )


def _handheld(
    s: VideoSettings,
    src: Source,
    seconds: float,
    rng: random.Random,
) -> Handheld:
    if not any((s.tilt, s.perspective, s.shake, s.drift)):
        return Handheld()

    return Handheld.sample(
        rng,
        tilt=s.tilt,
        perspective=s.perspective,
        shake=s.shake,
        drift=s.drift,
        seconds=seconds,
        aspect=src.aspect,
        constants=s.constants.handheld,
    )


def _sized(size: tuple[int, int], factor: float | None) -> Size:
    w, h = size
    if factor is None:
        return Size(width=w, height=h)
    out_w = even(w * factor)
    return Size(width=out_w, height=even(h * out_w / w))


def _sign(rng: random.Random) -> int:
    return rng.choice((1, -1))


def _stretch(s: VideoSettings, rng: random.Random) -> _Stretch | None:
    if s.stretch is None:
        return None
    return _Stretch(x=draw(s.stretch, rng) or 1.0, y=draw(s.stretch, rng) or 1.0)


def _chroma(s: VideoSettings, rng: random.Random) -> _Shift | None:
    x, y = draw_int(s.chroma_px, rng), draw_int(s.chroma_px, rng)
    if not x:
        return None
    return _Shift(x=_sign(rng) * x, y=_sign(rng) * (y or 0))


def _rotate(s: VideoSettings, rng: random.Random) -> float | None:
    angle = draw(s.rotate, rng)
    if angle is None or not s.rotate_both_ways:
        return angle
    return angle * _sign(rng)


def _rotate_cover(rotate: float | None, aspect: float, eps: float) -> float | None:
    if rotate is None or abs(rotate) < eps:
        return None
    a = math.radians(abs(rotate))
    return math.cos(a) + aspect * math.sin(a) + 0.01


def _draw(
    s: VideoSettings,
    src: Source,
    size: tuple[int, int],
    rng: random.Random,
) -> VideoDraws:
    timeline = _timeline(s, src, rng)
    tone_mid, tone_shoulder = draw_int(s.tone_mid, rng), draw_int(s.tone_shoulder, rng)
    rotate = _rotate(s, rng)
    output_size = _sized(size, draw(s.size, rng))
    mirror = chance(s.mirror, rng)
    rotate_cover = _rotate_cover(rotate, src.aspect, s.constants.handheld.warp_eps)
    zoom = draw(s.zoom, rng)
    shift_y = draw(s.shift_y, rng)
    corner = (rng.randrange(2), rng.randrange(2)) if s.corner_zoom is not None else None
    corner_zoom = draw(s.corner_zoom, rng)
    stretch = _stretch(s, rng)
    handheld = _handheld(s, src, timeline.duration_s, rng)
    saturation = draw(s.saturation, rng)
    contrast = draw(s.contrast, rng)
    brightness = draw(s.brightness, rng)
    gamma = draw(s.gamma, rng)
    colortemp = draw_int(s.colortemp, rng)
    tint = draw(s.tint, rng)
    sharpen = draw(s.sharpen, rng)
    black_and_white = chance(s.black_and_white, rng)
    vignette = draw(s.vignette, rng)
    chroma = _chroma(s, rng)
    noise = draw(s.noise, rng)
    noise_seed = rng.randrange(1 << 31)
    exposure = draw(s.exposure, rng)
    look = pick(s.look, rng) or "any"
    border = draw(s.border, rng)
    pattern = pick(s.pattern, rng)
    pattern_seed = rng.randrange(1 << 31)
    pattern_opacity = draw(s.pattern_opacity, rng)
    sharpen_late = draw(s.sharpen_late, rng)
    particles = draw(s.particles, rng)
    return VideoDraws(
        timeline=timeline,
        output_size=(output_size.width, output_size.height),
        mirror=mirror,
        rotate=rotate,
        rotate_cover=rotate_cover,
        zoom=zoom,
        shift_y=shift_y,
        corner=corner,
        corner_zoom=corner_zoom,
        stretch=(stretch.x, stretch.y) if stretch else None,
        handheld=handheld,
        saturation=saturation,
        contrast=contrast,
        brightness=brightness,
        gamma=gamma,
        colortemp=colortemp,
        tone=(tone_mid or 0, tone_shoulder or 0) if s.tone_mid is not None else None,
        tint=tint,
        sharpen=sharpen,
        black_and_white=black_and_white,
        vignette=vignette,
        chroma=(chroma.x, chroma.y) if chroma else None,
        noise=noise,
        noise_seed=noise_seed,
        exposure=exposure,
        look=look,
        border=border,
        pattern=pattern,
        pattern_seed=pattern_seed,
        pattern_opacity=pattern_opacity,
        sharpen_late=sharpen_late,
        particles=particles,
        adjust=AdjustDraws.sample(s.adjust, rng) if s.adjust else None,
    )


class VideoTransformer:
    def __init__(
        self,
        settings: VideoSettings,
        source: Source,
        output_size: Size,
        rng: random.Random,
    ) -> None:
        self.settings = settings
        size = (output_size.width, output_size.height)
        self.draws = _draw(settings, source, size, rng)

    @classmethod
    def from_draws(cls, settings: VideoSettings, draws: VideoDraws) -> Self:
        made = cls.__new__(cls)
        made.settings, made.draws = settings, draws
        return made

    @property
    def timeline(self) -> Timeline:
        return self.draws.timeline

    @property
    def _adjusting(self) -> AdjustConstants:
        adjust = self.settings.adjust
        return adjust.constants if adjust else AdjustConstants()

    @property
    def output_size(self) -> Size:
        width, height = self.draws.output_size
        return Size(width=width, height=height)

    def write_caption(self, work_dir: Path) -> Path | None:
        if not self.settings.snapchat_caption:
            return None
        return render_snapchat(
            self.settings.snapchat_caption,
            self.draws.output_size,
            work_dir / "snapchat.png",
        )

    def filters(
        self,
        work_dir: Path,
        scaler: str = "bicubic",
        caption_input: int | None = None,
        working_fps: float | None = None,
    ) -> str:
        d = self.draws
        w, h = d.output_size
        chain = [
            "setpts=PTS-STARTPTS",
            *self._working_frames(working_fps, scaler),
            *self._framing(),
            *self._rotation(),
            *d.handheld.filters(self.settings.constants.handheld),
            *self._colour(),
            *self._late(),
            *self._texture(),
            f"setpts=PTS/{d.timeline.speed}",
            f"scale={w}:{h}:flags={scaler}",
            *self._output_stage(),
        ]
        graph = ",".join(chain)
        overlay = self._overlay(work_dir)
        if caption_input is not None and self.settings.snapchat_caption:
            overlay = (overlay or "") + (
                f"[cap0];[{caption_input}:v]format=rgba[cap1];[cap0][cap1]overlay=0:0"
            )
        return (
            f"{graph}{overlay},format=yuv420p" if overlay else f"{graph},format=yuv420p"
        )

    def _working_frames(
        self,
        working_fps: float | None,
        scaler: str,
    ) -> list[str]:
        draws = self.draws
        chain: list[str] = []
        if working_fps is not None:
            chain.append(f"fps={working_fps:.4f}")
        width = even(draws.output_size[0] * self.settings.constants.work_headroom)
        chain.append(f"scale=w=min(iw\\,{width}):h=-2:flags={scaler}")
        return chain

    def _framing(self) -> list[str]:
        d = self.draws
        chain = ["hflip"] if d.mirror else []
        if d.zoom and d.zoom > 1:
            down = d.shift_y or 0.0
            chain.append(
                f"crop=trunc(iw/{d.zoom:.4f}/2)*2:trunc(ih/{d.zoom:.4f}/2)*2"
                f":(iw-ow)/2:max(0\\,min(ih-oh\\,(ih-oh)/2-ih*{down:.4f}))",
            )
        if d.corner and d.corner_zoom and d.corner_zoom > 1:
            x = "iw-ow" if d.corner[0] else "0"
            y = "ih-oh" if d.corner[1] else "0"
            z = f"{d.corner_zoom:.4f}"
            chain.append(f"crop=trunc(iw/{z}/2)*2:trunc(ih/{z}/2)*2:{x}:{y}")
        if d.stretch and max(d.stretch) > 1:
            sx, sy = (max(1.0, v) for v in d.stretch)
            chain.append(f"crop=trunc(iw/{sx:.4f}/2)*2:trunc(ih/{sy:.4f}/2)*2")
        return chain

    def _rotation(self) -> list[str]:
        d = self.draws
        if d.rotate is None or d.rotate_cover is None:
            return []
        o = d.rotate_cover
        return [
            f"scale=w=trunc(iw*{o:.5f}/2)*2:h=trunc(ih*{o:.5f}/2)*2",
            (
                f"rotate={d.rotate:.4f}*PI/180:"
                "ow=trunc(iw/2)*2:oh=trunc(ih/2)*2:fillcolor=black"
            ),
            f"crop=w=trunc(iw/{o:.5f}/2)*2:h=trunc(ih/{o:.5f}/2)*2",
        ]

    def _colour(self) -> list[str]:
        d = self.draws
        eq = [
            f"{name}={value:.4f}"
            for name, value in (
                ("saturation", d.saturation),
                ("contrast", d.contrast),
                ("brightness", d.brightness),
                ("gamma", d.gamma),
            )
            if value is not None
        ]
        chain = [f"eq={':'.join(eq)}"] if eq else []
        if d.colortemp is not None:
            chain.append(f"colortemperature=temperature={d.colortemp}")
        if d.tone is not None:
            chain.append(_tone_curve(*d.tone))
        if d.tint:
            chain.append(f"colorbalance=rs={d.tint:.4f}:bs={-d.tint:.4f}")
        if d.adjust:
            chain += d.adjust.colour(self._adjusting)
        if d.sharpen:
            chain.append(f"unsharp=5:5:{d.sharpen:.3f}:5:5:0.0")
        if d.black_and_white:
            chain.append("hue=s=0")
        return chain

    def _late(self) -> list[str]:
        d = self.draws
        chain = []
        if d.vignette:
            angle = d.vignette * self.settings.constants.right_angle
            chain.append(f"vignette=a={angle:.4f}")
        if d.adjust:
            chain += d.adjust.vignette_filters(self._adjusting)
        if d.chroma:
            cb, cv = d.chroma
            chain.append(f"chromashift=cbh={cb}:crh={-cb}:cbv={cv}")
        if d.border and d.look in ("border", "any"):
            chain.append(_border(d.border))
        return chain

    def _texture(self) -> list[str]:
        d = self.draws
        chain = []
        if d.noise and d.noise >= self.settings.constants.noise_min:
            chain.append(
                f"noise=alls={d.noise:.2f}:allf={self.settings.noise_flags}"
                f":all_seed={d.noise_seed}",
            )
        if d.exposure:
            chain.append(f"exposure=exposure={d.exposure:.4f}")
        if d.adjust:
            chain += d.adjust.exposure_filters(self._adjusting)
        return chain

    def _output_stage(self) -> list[str]:
        d = self.draws
        chain = []
        if d.sharpen_late:
            chain.append(f"unsharp=5:5:{d.sharpen_late:.4f}")
        if d.adjust:
            seed = (d.noise_seed + 1) % (1 << 31)
            chain += d.adjust.output_filters(seed, self._adjusting)
        if d.particles and d.particles >= self.settings.constants.noise_min:
            seed = (d.noise_seed + 2) % (1 << 31)
            chain.append(
                f"format=yuv420p,noise=alls={d.particles:.2f}:allf=t:all_seed={seed}",
            )
        return chain

    def _overlay(self, work_dir: Path) -> str | None:
        d = self.draws
        if not d.pattern or not d.pattern_opacity or d.look not in ("pattern", "any"):
            return None
        image = write_pattern(
            Pattern(d.pattern),
            d.output_size,
            d.pattern_seed,
            work_dir / "pattern.png",
            self.settings.constants.patterns,
        )
        return (
            f"[ov0];movie={_graph_path(image)},format=rgba,"
            f"colorchannelmixer=aa={d.pattern_opacity:.4f}[ov1];[ov0][ov1]overlay=0:0"
        )


def _tone_curve(mid: int, shoulder: int) -> str:
    q1 = min(110, max(32, 64 + shoulder))
    q2 = min(236, max(q1 + 6, 128 + mid))
    q3 = min(250, max(q2 + 6, 192 + shoulder))
    points = " ".join(
        f"{x / 255:.4f}/{y / 255:.4f}" for x, y in ((64, q1), (128, q2), (192, q3))
    )
    return f"curves=all='0/0 {points} 1/1'"


def _border(share: float) -> str:
    s = f"{share:.4f}"
    inset = f"2*trunc(min(iw\\,ih)*{s})"
    return (
        "split[bd0][bd1];"
        f"[bd1]boxblur=luma_radius=min(w\\,h)*{s}:luma_power=3[bd2];"
        f"[bd0]scale=w=iw-{inset}:h=ih-{inset}[bd3];"
        "[bd2][bd3]overlay=x=(W-w)/2:y=(H-h)/2"
    )


def _graph_path(path: Path) -> str:
    text = path.as_posix()
    if not all(c.isalnum() or c in "/._-:" for c in text):
        msg = f"unsafe path for a filtergraph: {text!r}"
        raise ValueError(msg)
    return text.replace(":", "\\:")
