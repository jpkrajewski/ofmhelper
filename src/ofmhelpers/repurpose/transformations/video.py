"""The picture side of a repurposed copy.

`VideoSettings` is the video part of a preset: every knob optional, a
missing knob is off (values: `repurpose.values`). `VideoTransformer` draws
the knobs once for one copy and builds the ffmpeg filter chain, always in
this order:

1. mirror, then the framing crops: centred zoom (moved down by `shift_y`),
   zoom into a random corner, the directional stretch, a fixed `rotate`
   (scaled up so the turned frame still covers, cropped back);
2. the handheld look: tilt, perspective, shake, drift (`handheld.py`);
3. colour: eq (saturation / contrast / brightness / gamma),
   colortemperature, a luma tone curve, a shadow tint, CapCut's Adjust
   colour sliders, sharpen, B&W;
4. vignette (+ Adjust's), a 1-2 px chroma shift, the blurred border;
5. grain (its own seed every copy), then exposure (+ Adjust's; exposure
   outputs float RGB, which the noise filter would turn into colour static);
6. speed, then the scale to the exact output size;
7. after it: late sharpen, Adjust's sharpen / clarity / particles,
   particles, the pattern overlay, the snapchat caption (`extras/snapchat`),
   yuv420p.

Knobs come from: legacy `utils.uniqify` (speed, eq, colortemperature,
sharpen, zoom, stretch, mirror, B&W, grain), InstaLabs Spoofer (handheld),
Duplyfi 1.5.1 (gamma, tone curve, tint, corner zoom, vignette, chroma shift,
border, patterns, size, cuts), `utils.capcut` (shift_y, exposure, late
sharpen, particles, trims in seconds) and CapCut's Adjust panel (`adjust`,
in CapCut's slider units: `adjust.py`).
"""

import math
import random
from pathlib import Path
from typing import Self

from pydantic import BaseModel

from ofmhelpers.repurpose.extras.snapchat import render as render_snapchat
from ofmhelpers.repurpose.probe import Source
from ofmhelpers.repurpose.transformations.adjust import AdjustDraws, AdjustSettings
from ofmhelpers.repurpose.transformations.handheld import _WARP_EPS, Handheld
from ofmhelpers.repurpose.transformations.patterns import Pattern
from ofmhelpers.repurpose.transformations.patterns import write as write_pattern
from ofmhelpers.repurpose.values import Value, chance, draw, draw_int, even, pick

# Kept after every cut, at least: the cuts never eat the clip.
_MIN_KEPT_SHARE = 0.5
_RIGHT_ANGLE = 1.5708
# Grain below this strength stays out of the chain.
_NOISE_MIN = 0.05


class VideoSettings(BaseModel, frozen=True):
    """The video part of a preset; see the module docstring for the order."""

    # Timeline: seconds / shares of the clip cut off, playback speed.
    trim_head_s: Value | None = None
    trim_tail_s: Value | None = None
    cut_head: Value | None = None
    cut_tail: Value | None = None
    speed: Value | None = None
    # Framing.
    mirror: float | None = None
    rotate: Value | None = None
    zoom: Value | None = None
    shift_y: Value | None = None
    corner_zoom: Value | None = None
    stretch: Value | None = None
    tilt: Value | None = None
    perspective: Value | None = None
    shake: Value | None = None
    drift: Value | None = None
    # Colour.
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
    # Texture and the obvious looks.
    noise: Value | None = None
    noise_flags: str = "t"
    exposure: Value | None = None
    border: Value | None = None
    pattern: list[str] | str | None = None
    pattern_opacity: Value | None = None
    # One of "border" / "pattern" / "none" per copy; unset: both as set.
    look: list[str] | None = None
    # At the output size.
    size: Value | None = None
    sharpen_late: Value | None = None
    particles: Value | None = None
    # A Snapchat caption bar (extras/snapchat) with this text, overlaid last;
    # None = no caption.
    snapchat_caption: str | None = None
    # CapCut's Adjust panel, in its slider units.
    adjust: AdjustSettings | None = None

    def merged(self, extra: "VideoSettings | None") -> "VideoSettings":
        """These settings with `extra`'s set knobs on top."""
        if extra is None:
            return self
        return self.model_copy(update=extra.model_dump(exclude_unset=True))


class Timeline(BaseModel, frozen=True):
    start_s: float
    duration_s: float
    speed: float


class VideoDraws(BaseModel, frozen=True):
    """One copy's drawn picture values (None: knob off)."""

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
    # Blue-difference plane moved (x, y) px; red-difference the other way in x.
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


def _cuts(s: VideoSettings, src: Source, rng: random.Random) -> tuple[float, float]:
    """Seconds off the start and off the end; together at most half."""
    head = (draw(s.trim_head_s, rng) or 0.0) + (
        draw(s.cut_head, rng) or 0.0
    ) * src.duration
    tail = (draw(s.trim_tail_s, rng) or 0.0) + (
        draw(s.cut_tail, rng) or 0.0
    ) * src.duration
    limit = src.duration * (1 - _MIN_KEPT_SHARE)
    if head + tail > limit:
        scale = limit / (head + tail)
        head, tail = head * scale, tail * scale
    return head, tail


def _timeline(s: VideoSettings, src: Source, rng: random.Random) -> Timeline:
    head, tail = _cuts(s, src, rng)
    return Timeline(
        start_s=round(head, 3),
        duration_s=round(src.duration - head - tail, 3),
        speed=round(draw(s.speed, rng) or 1.0, 4),
    )


def _handheld(
    s: VideoSettings, src: Source, seconds: float, rng: random.Random
) -> Handheld:
    if not any((s.tilt, s.perspective, s.shake, s.drift)):
        return Handheld()

    def span(v: Value | None) -> tuple[float, float]:
        if v is None:
            return (0.0, 0.0)
        return v if isinstance(v, tuple) else (v, v)

    return Handheld.sample(
        rng,
        tilt=span(s.tilt),
        perspective=span(s.perspective),
        shake=span(s.shake),
        drift=span(s.drift),
        seconds=seconds,
        aspect=src.aspect,
    )


def _sized(size: tuple[int, int], factor: float | None) -> tuple[int, int]:
    if factor is None:
        return size
    w, h = size
    out_w = even(w * factor)
    return out_w, even(h * out_w / w)


def _sign(rng: random.Random) -> int:
    return rng.choice((1, -1))


def _stretch(s: VideoSettings, rng: random.Random) -> tuple[float, float] | None:
    if s.stretch is None:
        return None
    return (draw(s.stretch, rng) or 1.0, draw(s.stretch, rng) or 1.0)


def _chroma(s: VideoSettings, rng: random.Random) -> tuple[int, int] | None:
    x, y = draw_int(s.chroma_px, rng), draw_int(s.chroma_px, rng)
    if not x:
        return None
    return (_sign(rng) * x, _sign(rng) * (y or 0))


def _rotate(s: VideoSettings, rng: random.Random) -> float | None:
    """The drawn rotation in degrees; None = knob off (0 turns nothing)."""
    return draw(s.rotate, rng)


def _rotate_cover(rotate: float | None, aspect: float) -> float | None:
    """The overscan a rotated frame needs to still cover the output."""
    if rotate is None or abs(rotate) < _WARP_EPS:
        return None
    a = math.radians(abs(rotate))
    return math.cos(a) + aspect * math.sin(a) + 0.01


def _draw(
    s: VideoSettings, src: Source, size: tuple[int, int], rng: random.Random
) -> VideoDraws:
    timeline = _timeline(s, src, rng)
    tone_mid, tone_shoulder = draw_int(s.tone_mid, rng), draw_int(s.tone_shoulder, rng)
    rotate = _rotate(s, rng)
    return VideoDraws(
        timeline=timeline,
        output_size=_sized(size, draw(s.size, rng)),
        mirror=chance(s.mirror, rng),
        rotate=rotate,
        rotate_cover=_rotate_cover(rotate, src.aspect),
        zoom=draw(s.zoom, rng),
        shift_y=draw(s.shift_y, rng),
        corner=(rng.randrange(2), rng.randrange(2))
        if s.corner_zoom is not None
        else None,
        corner_zoom=draw(s.corner_zoom, rng),
        stretch=_stretch(s, rng),
        handheld=_handheld(s, src, timeline.duration_s, rng),
        saturation=draw(s.saturation, rng),
        contrast=draw(s.contrast, rng),
        brightness=draw(s.brightness, rng),
        gamma=draw(s.gamma, rng),
        colortemp=draw_int(s.colortemp, rng),
        tone=(tone_mid or 0, tone_shoulder or 0) if s.tone_mid is not None else None,
        tint=draw(s.tint, rng),
        sharpen=draw(s.sharpen, rng),
        black_and_white=chance(s.black_and_white, rng),
        vignette=draw(s.vignette, rng),
        chroma=_chroma(s, rng),
        noise=draw(s.noise, rng),
        noise_seed=rng.randrange(1 << 31),
        exposure=draw(s.exposure, rng),
        look=pick(s.look, rng) or "any",
        border=draw(s.border, rng),
        pattern=pick(s.pattern, rng),
        pattern_seed=rng.randrange(1 << 31),
        pattern_opacity=draw(s.pattern_opacity, rng),
        sharpen_late=draw(s.sharpen_late, rng),
        particles=draw(s.particles, rng),
        adjust=AdjustDraws.sample(s.adjust, rng) if s.adjust else None,
    )


class VideoTransformer:
    """One copy's picture: drawn on creation, then `filters()`."""

    def __init__(
        self,
        settings: VideoSettings,
        source: Source,
        output_size: tuple[int, int],
        rng: random.Random,
    ) -> None:
        self.settings = settings
        self.draws = _draw(settings, source, output_size, rng)

    @classmethod
    def from_draws(cls, settings: VideoSettings, draws: VideoDraws) -> Self:
        """A transformer for values already drawn (the lab's quality
        reference: a copy's framing without its look)."""
        made = cls.__new__(cls)
        made.settings, made.draws = settings, draws
        return made

    @property
    def timeline(self) -> Timeline:
        return self.draws.timeline

    @property
    def output_size(self) -> tuple[int, int]:
        return self.draws.output_size

    def write_caption(self, work_dir: Path) -> Path | None:
        """The snapchat caption PNG (`extras/snapchat`), if the settings name
        one; `command.build` adds it as a second input and overlays it last."""
        if not self.settings.snapchat_caption:
            return None
        return render_snapchat(
            self.settings.snapchat_caption,
            self.draws.output_size,
            work_dir / "snapchat.png",
        )

    def filters(
        self, work_dir: Path, scaler: str = "bicubic", caption_input: int | None = None
    ) -> str:
        """The whole -vf graph; the pattern image (if any) goes to `work_dir`.
        `caption_input`: the ffmpeg input index of the caption PNG when
        `command.build` added one; None drops the caption overlay."""
        d = self.draws
        w, h = d.output_size
        chain = [
            # Start at 0: after an input seek the first frame can land at
            # +30 ms, and the iphones recorder then starts the video after
            # the whole audio (movie twice as long; 2026-10-26).
            "setpts=PTS-STARTPTS",
            *self._framing(),
            *self._rotation(),
            *d.handheld.filters(),
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

    def _framing(self) -> list[str]:
        d = self.draws
        chain = ["hflip"] if d.mirror else []
        if d.zoom and d.zoom > 1:
            down = d.shift_y or 0.0
            chain.append(
                f"crop=trunc(iw/{d.zoom:.4f}/2)*2:trunc(ih/{d.zoom:.4f}/2)*2"
                f":(iw-ow)/2:max(0\\,min(ih-oh\\,(ih-oh)/2-ih*{down:.4f}))"
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
        """A fixed rotation: scaled up so the turned frame still covers the
        output, turned, cropped back (same overscan idea as the handheld)."""
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
            chain += d.adjust.colour()
        if d.sharpen:
            chain.append(f"unsharp=5:5:{d.sharpen:.3f}:5:5:0.0")
        if d.black_and_white:
            chain.append("hue=s=0")
        return chain

    def _late(self) -> list[str]:
        d = self.draws
        chain = []
        if d.vignette:
            chain.append(f"vignette=a={d.vignette * _RIGHT_ANGLE:.4f}")
        if d.adjust:
            chain += d.adjust.vignette_filters()
        if d.chroma:
            cb, cv = d.chroma
            chain.append(f"chromashift=cbh={cb}:crh={-cb}:cbv={cv}")
        if d.border and d.look in ("border", "any"):
            chain.append(_border(d.border))
        return chain

    def _texture(self) -> list[str]:
        d = self.draws
        chain = []
        if d.noise and d.noise >= _NOISE_MIN:
            chain.append(
                f"noise=alls={d.noise:.2f}:allf={self.settings.noise_flags}"
                f":all_seed={d.noise_seed}"
            )
        if d.exposure:
            chain.append(f"exposure=exposure={d.exposure:.4f}")
        if d.adjust:
            chain += d.adjust.exposure_filters()
        return chain

    def _output_stage(self) -> list[str]:
        d = self.draws
        chain = []
        if d.sharpen_late:
            chain.append(f"unsharp=5:5:{d.sharpen_late:.4f}")
        if d.adjust:
            # Seeds off the copy's noise seed: no new draw, nothing else shifts.
            chain += d.adjust.output_filters(seed=(d.noise_seed + 1) % (1 << 31))
        if d.particles and d.particles >= _NOISE_MIN:
            seed = (d.noise_seed + 2) % (1 << 31)
            chain.append(
                f"format=yuv420p,noise=alls={d.particles:.2f}:allf=t:all_seed={seed}"
            )
        return chain

    def _overlay(self, work_dir: Path) -> str | None:
        """The pattern laid over the output-size frame at a few percent."""
        d = self.draws
        if not d.pattern or not d.pattern_opacity or d.look not in ("pattern", "any"):
            return None
        image = write_pattern(
            Pattern(d.pattern), d.output_size, d.pattern_seed, work_dir / "pattern.png"
        )
        return (
            f"[ov0];movie={_graph_path(image)},format=rgba,"
            f"colorchannelmixer=aa={d.pattern_opacity:.4f}[ov1];[ov0][ov1]overlay=0:0"
        )


def _tone_curve(mid: int, shoulder: int) -> str:
    """Duplyfi's luma curve: the 64/128/192 points moved by code values."""
    q1 = min(110, max(32, 64 + shoulder))
    q2 = min(236, max(q1 + 6, 128 + mid))
    q3 = min(250, max(q2 + 6, 192 + shoulder))
    points = " ".join(
        f"{x / 255:.4f}/{y / 255:.4f}" for x, y in ((64, q1), (128, q2), (192, q3))
    )
    return f"curves=all='0/0 {points} 1/1'"


def _border(share: float) -> str:
    """The picture shrunk inside a blurred copy of itself (one graph segment;
    commas inside expressions escaped)."""
    s = f"{share:.4f}"
    inset = f"2*trunc(min(iw\\,ih)*{s})"
    return (
        "split[bd0][bd1];"
        f"[bd1]boxblur=luma_radius=min(w\\,h)*{s}:luma_power=3[bd2];"
        f"[bd0]scale=w=iw-{inset}:h=ih-{inset}[bd3];"
        "[bd2][bd3]overlay=x=(W-w)/2:y=(H-h)/2"
    )


def _graph_path(path: Path) -> str:
    """A file path as a filtergraph value: plain characters only, so nothing
    needs escaping but a Windows drive colon, which the filter parser reads
    as an option separator (`C:` -> `C\\:`)."""
    text = path.as_posix()
    if not all(c.isalnum() or c in "/._-:" for c in text):
        msg = f"unsafe path for a filtergraph: {text!r}"
        raise ValueError(msg)
    return text.replace(":", "\\:")
