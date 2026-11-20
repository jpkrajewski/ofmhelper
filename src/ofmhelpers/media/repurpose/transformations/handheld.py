import math
lazy import random

from pydantic import BaseModel

lazy from ofmhelpers.media.repurpose.values import Value

Range = tuple[float, float]

_SHAKE_BANDS = ((0.15, 0.45, 0.55), (1.2, 2.6, 0.3), (3.5, 6.5, 0.15))
_DRIFT_CYCLES: Range = (0.15, 0.3)
_MIN_SECONDS = 0.5
_OVERSCAN_SPARE = 0.01
_WARP_EPS = 1e-4


class Wave(BaseModel, frozen=True):
    amp: float
    freq: float
    phase: float

    def expr(self) -> str:
        return f"{self.amp:.6f}*sin(2*PI*{self.freq:.5f}*t+{self.phase:.4f})"


def _wave(rng: random.Random, amp: float, freq: Range) -> Wave:
    return Wave(amp=amp, freq=rng.uniform(*freq), phase=rng.uniform(0.0, 2 * math.pi))


def _uniform(rng: random.Random, bounds: Value | None) -> float:
    if bounds is None:
        return rng.uniform(0.0, 0.0)
    if isinstance(bounds, tuple):
        return rng.uniform(*bounds)
    return rng.uniform(bounds, bounds)


def _motion(
    rng: random.Random, shake: float, drift: float, seconds: float
) -> list[Wave]:
    waves = [
        _wave(rng, shake * share * rng.uniform(0.8, 1.2), (lo, hi))
        for lo, hi, share in _SHAKE_BANDS
    ]
    cycles = (_DRIFT_CYCLES[0] / seconds, _DRIFT_CYCLES[1] / seconds)
    waves.append(_wave(rng, drift, cycles))
    return [w for w in waves if w.amp > 0]


class Handheld(BaseModel, frozen=True):
    tilt_deg: float = 0.0
    corners: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    x: tuple[Wave, ...] = ()
    y: tuple[Wave, ...] = ()
    aspect: float = 1.0

    @classmethod
    def sample(
        cls,
        rng: random.Random,
        *,
        tilt: Value | None,
        perspective: Value | None,
        shake: Value | None,
        drift: Value | None,
        seconds: float,
        aspect: float,
    ) -> Handheld:
        skew = _uniform(rng, perspective)
        top, bottom, left, right = (rng.uniform(-skew, skew) for _ in range(4))
        shake_amp = _uniform(rng, shake)
        drift_amp = _uniform(rng, drift)
        seconds = max(seconds, _MIN_SECONDS)
        return cls(
            tilt_deg=round(_uniform(rng, tilt), 3),
            corners=(top, bottom, left, right),
            x=tuple(_motion(rng, shake_amp, drift_amp, seconds)),
            y=tuple(
                _motion(
                    rng,
                    shake_amp * rng.uniform(0.6, 0.9),
                    drift_amp * rng.uniform(0.5, 1.0),
                    seconds,
                )
            ),
            aspect=aspect,
        )

    @property
    def warped(self) -> bool:
        return max(abs(self.tilt_deg), *map(abs, self.corners)) > _WARP_EPS

    @property
    def reach(self) -> float:
        return max(sum(w.amp for w in self.x), sum(w.amp for w in self.y))

    @property
    def overscan(self) -> float:
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
    motion = "+".join(w.expr() for w in waves) or "0"
    free = f"({inner}-{outer})"
    return f"max(0\\,min({free}\\,{free}/2+min(ow\\,oh)*({motion})))"
