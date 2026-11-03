"""A handheld-camera look for one video: a small tilt, a perspective skew,
camera shake and a slow drift of the frame.

Ported from the InstaLabs Spoofer's geometry stage (`uniq/filters/
geometry.py`, decompiled 2026-10-06): the frame is scaled up just enough
(the overscan) that the warped picture and the moving crop window never show
an edge, warped by `perspective` (the tilt is the quad's corners turned about
the centre), then cropped back to its own size by a window that moves with
`t`: three sines per axis for the shake (slow sway, hand tremor, fine
jitter), plus one slow sine across the clip for the drift.

Amounts are shares of the frame, so any source size gets the same look. The
numbers live here, the frame size stays in ffmpeg's own expressions (W/H,
iw/ih): ffmpeg turns a phone video upright before the filters, so a probed
width/height may be the other way round.
"""

import math
import random

from pydantic import BaseModel

Range = tuple[float, float]

# (min Hz, max Hz, share of the amplitude) per shake band: slow sway, hand
# tremor, fine jitter -- InstaLabs' bands.
_SHAKE_BANDS = ((0.15, 0.45, 0.55), (1.2, 2.6, 0.3), (3.5, 6.5, 0.15))
# The drift's sine runs this share of one cycle over the clip.
_DRIFT_CYCLES: Range = (0.15, 0.3)
# A clip shorter than this drifts as if it were this long.
_MIN_SECONDS = 0.5
# Spare overscan, so rounding never shows a one-pixel edge.
_OVERSCAN_SPARE = 0.01
# A tilt or corner offset this close to zero is no warp at all.
_WARP_EPS = 1e-4


class Wave(BaseModel, frozen=True):
    """amp * sin(2 pi freq t + phase); amp is a share of the frame's short side."""

    amp: float
    freq: float
    phase: float

    def expr(self) -> str:
        return f"{self.amp:.6f}*sin(2*PI*{self.freq:.5f}*t+{self.phase:.4f})"


def _wave(rng: random.Random, amp: float, freq: Range) -> Wave:
    return Wave(amp=amp, freq=rng.uniform(*freq), phase=rng.uniform(0.0, 2 * math.pi))


def _motion(
    rng: random.Random, shake: float, drift: float, seconds: float
) -> tuple[Wave, ...]:
    """One axis: the shake bands, then the drift (its random phase also picks
    the direction it wanders in)."""
    waves = [
        _wave(rng, shake * share * rng.uniform(0.8, 1.2), (lo, hi))
        for lo, hi, share in _SHAKE_BANDS
    ]
    cycles = (_DRIFT_CYCLES[0] / seconds, _DRIFT_CYCLES[1] / seconds)
    waves.append(_wave(rng, drift, cycles))
    return tuple(w for w in waves if w.amp > 0)


class Handheld(BaseModel, frozen=True):
    """One copy's camera look; the default is a still camera (no filters)."""

    tilt_deg: float = 0.0
    # Shares of the frame: the top and bottom edge's corners moved in along x,
    # the left and right edge's along y (negative: out).
    corners: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    x: tuple[Wave, ...] = ()
    y: tuple[Wave, ...] = ()
    # The frame's long side over its short side.
    aspect: float = 1.0

    @classmethod
    def sample(
        cls,
        rng: random.Random,
        *,
        tilt: Range,
        perspective: Range,
        shake: Range,
        drift: Range,
        seconds: float,
        aspect: float,
    ) -> "Handheld":
        skew = rng.uniform(*perspective)
        top, bottom, left, right = (rng.uniform(-skew, skew) for _ in range(4))
        shake_amp = rng.uniform(*shake)
        drift_amp = rng.uniform(*drift)
        seconds = max(seconds, _MIN_SECONDS)
        return cls(
            tilt_deg=round(rng.uniform(*tilt), 3),
            corners=(top, bottom, left, right),
            x=_motion(rng, shake_amp, drift_amp, seconds),
            y=_motion(
                rng,
                shake_amp * rng.uniform(0.6, 0.9),
                drift_amp * rng.uniform(0.5, 1.0),
                seconds,
            ),
            aspect=aspect,
        )

    @property
    def warped(self) -> bool:
        return max(abs(self.tilt_deg), *map(abs, self.corners)) > _WARP_EPS

    @property
    def reach(self) -> float:
        """How far the crop window moves off centre, at most, as a share of
        the short side."""
        return max(sum(w.amp for w in self.x), sum(w.amp for w in self.y))

    @property
    def overscan(self) -> float:
        """The scale-up after which a frame turned by the tilt still covers
        the output, and the warp and the moving window stay inside it."""
        a = math.radians(abs(self.tilt_deg))
        cover = math.cos(a) + self.aspect * math.sin(a)
        skew = 1 + 2 * max(map(abs, self.corners))
        return cover * skew + 2 * self.reach + _OVERSCAN_SPARE

    def filters(self) -> list[str]:
        if not self.warped and not self.reach:
            return []
        o = self.overscan
        chain = [f"scale=w=trunc(iw*{o:.5f}/2)*2:h=trunc(ih*{o:.5f}/2)*2"]
        if self.warped:
            chain.append(self._perspective())
        chain.append(
            f"crop=w=trunc(iw/{o:.5f}/2)*2:h=trunc(ih/{o:.5f}/2)*2"
            f":x={_window(self.x, 'iw', 'ow')}:y={_window(self.y, 'ih', 'oh')}"
        )
        return chain

    def _perspective(self) -> str:
        """Where each corner of the source lands (TL, TR, BL, BR), skewed and
        then turned about the centre, as `a*W+b*H` per coordinate."""
        a = math.radians(self.tilt_deg)
        ca, sa = math.cos(a), math.sin(a)
        top, bottom, left, right = self.corners
        quad = (
            (top, left),
            (1 - top, right),
            (bottom, 1 - left),
            (1 - bottom, 1 - right),
        )
        coords = []
        for i, (fx, fy) in enumerate(quad):
            dx, dy = fx - 0.5, fy - 0.5
            x = f"{0.5 + dx * ca:.6f}*W{-dy * sa:+.6f}*H"
            y = f"{dx * sa:.6f}*W{0.5 + dy * ca:+.6f}*H"
            coords.append(f"x{i}={x}:y{i}={y}")
        return f"perspective={':'.join(coords)}:sense=destination:interpolation=linear"


def _window(waves: tuple[Wave, ...], inner: str, outer: str) -> str:
    """The crop's offset on one axis: centred, moved by the waves, clamped
    inside the frame. Commas escaped: the filtergraph splits on them first."""
    motion = "+".join(w.expr() for w in waves) or "0"
    free = f"({inner}-{outer})"
    return f"max(0\\,min({free}\\,{free}/2+min(ow\\,oh)*({motion})))"
