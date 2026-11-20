import random

from pydantic import BaseModel

from ofmhelpers.media.repurpose.values import Value, draw

_EXPOSURE_STOPS_PER_UNIT = 1 / 150
_CONTRAST_GAMMA_PER_UNIT = 1 / 200
_CONTRAST_SATURATION_PER_UNIT = 1 / 90
_SHARPEN_PER_UNIT = 1 / 50
_PARTICLES_GRAIN_PER_UNIT = 0.1
_TEMP_K_PER_UNIT = 40
_NEUTRAL_K = 6500
_TINT_PER_UNIT = 1 / 250
_SATURATION_PER_UNIT = 1 / 100
_LIGHTNESS_PER_UNIT = 1 / 500
_CURVE_PER_UNIT = 0.002
_BRILLIANCE_SHADOW_PER_UNIT = 0.0012
_BRILLIANCE_HIGHLIGHT_PER_UNIT = 0.0008
_FADE_BLACK_PER_UNIT = 0.0025
_FADE_WHITE_PER_UNIT = 0.0005
_CLARITY_PER_UNIT = 0.006
_CLARITY_SIZE = 13
_VIGNETTE_ANGLE_PER_UNIT = 0.7854 / 50
_MIN_STEP = 0.01


class AdjustSettings(BaseModel, frozen=True):
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
        return cls(
            **{
                name: draw(getattr(s, name), rng)
                for name in AdjustSettings.model_fields
            }
        )

    def colour(self) -> list[str]:
        chain = []
        if self.temp:
            chain.append(
                f"colortemperature=temperature={_NEUTRAL_K - self.temp * _TEMP_K_PER_UNIT:.0f}"
            )
        if self.tint:
            g = -self.tint * _TINT_PER_UNIT
            chain.append(f"colorbalance=gs={g:.4f}:gm={g:.4f}:gh={g:.4f}")
        eq = self._eq()
        if eq:
            chain.append(eq)
        curve = self._curve()
        if curve:
            chain.append(curve)
        return chain

    def _eq(self) -> str | None:
        contrast = self.contrast or 0.0
        saturation = (1 + (self.saturation or 0.0) * _SATURATION_PER_UNIT) * (
            1 + contrast * _CONTRAST_SATURATION_PER_UNIT
        )
        gamma = 1 + contrast * _CONTRAST_GAMMA_PER_UNIT
        brightness = (self.lightness or 0.0) * _LIGHTNESS_PER_UNIT
        if (saturation, gamma, brightness) == (1, 1, 0):
            return None
        return f"eq=saturation={saturation:.4f}:gamma={gamma:.4f}:brightness={brightness:.4f}"

    def _curve(self) -> str | None:
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
        u = _CURVE_PER_UNIT
        brilliance, fade = self.brilliance or 0.0, self.fade or 0.0
        blacks, whites = self.blacks or 0.0, self.whites or 0.0
        y0 = max(0.0, blacks * u) + fade * _FADE_BLACK_PER_UNIT
        x0 = max(0.0, -blacks * u)
        y1 = 0.25 + (self.shadow or 0.0) * u + brilliance * _BRILLIANCE_SHADOW_PER_UNIT
        y3 = (
            0.75
            + (self.highlight or 0.0) * u
            - brilliance * _BRILLIANCE_HIGHLIGHT_PER_UNIT
        )
        y4 = 1 + min(0.0, whites * u) - fade * _FADE_WHITE_PER_UNIT
        x4 = 1 - max(0.0, whites * u)
        points = [(x0, y0), (0.25, y1), (0.5, 0.5), (0.75, y3), (x4, y4)]
        rising: list[tuple[float, float]] = []
        for x, y in points:
            low = rising[-1][1] + _MIN_STEP if rising else 0.0
            rising.append((x, min(1.0, max(low, y))))
        return "curves=all='" + " ".join(f"{x:.4f}/{y:.4f}" for x, y in rising) + "'"

    def exposure_filters(self) -> list[str]:
        if not self.exposure:
            return []
        return [f"exposure=exposure={self.exposure * _EXPOSURE_STOPS_PER_UNIT:.4f}"]

    def vignette_filters(self) -> list[str]:
        if not self.vignette:
            return []
        angle = abs(self.vignette) * _VIGNETTE_ANGLE_PER_UNIT
        mode = ":mode=backward" if self.vignette < 0 else ""
        return [f"vignette=a={angle:.4f}{mode}"]

    def output_filters(self, seed: int) -> list[str]:
        chain = []
        if self.sharpen:
            chain.append(f"unsharp=5:5:{self.sharpen * _SHARPEN_PER_UNIT:.4f}")
        if self.clarity:
            size = _CLARITY_SIZE
            chain.append(
                f"unsharp={size}:{size}:{self.clarity * _CLARITY_PER_UNIT:.4f}"
            )
        if self.particles:
            grain = self.particles * _PARTICLES_GRAIN_PER_UNIT
            chain.append(
                f"format=yuv420p,noise=alls={grain:.2f}:allf=t:all_seed={seed}"
            )
        return chain
