lazy import random

from pydantic import BaseModel, Field

from ofmhelpers.media.repurpose.transformations.constants import AdjustConstants
from ofmhelpers.media.repurpose.values import Knobs, Value, draw


class AdjustSettings(Knobs, frozen=True):
    constants: AdjustConstants = Field(default=AdjustConstants(), exclude=True)
    temp: Value | None = None
    tint: Value | None = None
    saturation: Value | None = None
    lightness: Value | None = None
    exposure: Value | None = None
    contrast: Value | None = None
    highlight: Value | None = None
    shadow: Value | None = None
    whites: Value | None = None
    blacks: Value | None = None
    brilliance: Value | None = None
    sharpen: Value | None = None
    clarity: Value | None = None
    particles: Value | None = None
    fade: Value | None = None
    vignette: Value | None = None


class AdjustDraws(BaseModel, frozen=True):
    temp: float | None
    tint: float | None
    saturation: float | None
    lightness: float | None
    exposure: float | None
    contrast: float | None
    highlight: float | None
    shadow: float | None
    whites: float | None
    blacks: float | None
    brilliance: float | None
    sharpen: float | None
    clarity: float | None
    particles: float | None
    fade: float | None
    vignette: float | None

    @classmethod
    def sample(cls, s: AdjustSettings, rng: random.Random) -> AdjustDraws:
        return cls(**{name: draw(getattr(s, name), rng) for name in cls.model_fields})

    def colour(self, c: AdjustConstants) -> list[str]:
        chain = []
        if self.temp:
            chain.append(
                f"colortemperature=temperature={c.neutral_k - self.temp * c.temp_k_per_unit:.0f}"
            )
        if self.tint:
            g = -self.tint * c.tint_per_unit
            chain.append(f"colorbalance=gs={g:.4f}:gm={g:.4f}:gh={g:.4f}")
        eq = self._eq(c)
        if eq:
            chain.append(eq)
        curve = self._curve(c)
        if curve:
            chain.append(curve)
        return chain

    def _eq(self, c: AdjustConstants) -> str | None:
        contrast = self.contrast or 0.0
        saturation = (1 + (self.saturation or 0.0) * c.saturation_per_unit) * (
            1 + contrast * c.contrast_saturation_per_unit
        )
        gamma = 1 + contrast * c.contrast_gamma_per_unit
        brightness = (self.lightness or 0.0) * c.lightness_per_unit
        if (saturation, gamma, brightness) == (1, 1, 0):
            return None
        return f"eq=saturation={saturation:.4f}:gamma={gamma:.4f}:brightness={brightness:.4f}"

    def _curve(self, c: AdjustConstants) -> str | None:
        knobs = (
            self.highlight,
            self.shadow,
            self.whites,
            self.blacks,
            self.brilliance,
            self.fade,
        )
        if not any(knobs):
            return None
        u = c.curve_per_unit
        brilliance, fade = self.brilliance or 0.0, self.fade or 0.0
        blacks, whites = self.blacks or 0.0, self.whites or 0.0
        y0 = max(0.0, blacks * u) + fade * c.fade_black_per_unit
        x0 = max(0.0, -blacks * u)
        y1 = 0.25 + (self.shadow or 0.0) * u + brilliance * c.brilliance_shadow_per_unit
        y3 = (
            0.75
            + (self.highlight or 0.0) * u
            - brilliance * c.brilliance_highlight_per_unit
        )
        y4 = 1 + min(0.0, whites * u) - fade * c.fade_white_per_unit
        x4 = 1 - max(0.0, whites * u)
        points = [(x0, y0), (0.25, y1), (0.5, 0.5), (0.75, y3), (x4, y4)]
        rising: list[tuple[float, float]] = []
        for x, y in points:
            low = rising[-1][1] + c.min_step if rising else 0.0
            rising.append((x, min(1.0, max(low, y))))
        return "curves=all='" + " ".join(f"{x:.4f}/{y:.4f}" for x, y in rising) + "'"

    def exposure_filters(self, c: AdjustConstants) -> list[str]:
        if not self.exposure:
            return []
        return [f"exposure=exposure={self.exposure * c.exposure_stops_per_unit:.4f}"]

    def vignette_filters(self, c: AdjustConstants) -> list[str]:
        if not self.vignette:
            return []
        angle = abs(self.vignette) * c.vignette_angle_per_unit
        mode = ":mode=backward" if self.vignette < 0 else ""
        return [f"vignette=a={angle:.4f}{mode}"]

    def output_filters(self, seed: int, c: AdjustConstants) -> list[str]:
        chain = []
        if self.sharpen:
            chain.append(f"unsharp=5:5:{self.sharpen * c.sharpen_per_unit:.4f}")
        if self.clarity:
            size = c.clarity_size
            chain.append(
                f"unsharp={size}:{size}:{self.clarity * c.clarity_per_unit:.4f}"
            )
        if self.particles:
            grain = self.particles * c.particles_grain_per_unit
            chain.append(
                f"format=yuv420p,noise=alls={grain:.2f}:allf=t:all_seed={seed}"
            )
        return chain
