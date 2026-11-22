from collections.abc import Mapping

from pydantic import BaseModel


class KnobConstants(BaseModel, frozen=True):
    neutral: Mapping[str, float] = frozendict()


class AdjustConstants(KnobConstants, frozen=True):
    exposure_stops_per_unit: float = 1 / 150
    contrast_gamma_per_unit: float = 1 / 200
    contrast_saturation_per_unit: float = 1 / 90
    sharpen_per_unit: float = 1 / 50
    particles_grain_per_unit: float = 0.1
    temp_k_per_unit: float = 40
    neutral_k: float = 6500
    tint_per_unit: float = 1 / 250
    saturation_per_unit: float = 1 / 100
    lightness_per_unit: float = 1 / 500
    curve_per_unit: float = 0.002
    brilliance_shadow_per_unit: float = 0.0012
    brilliance_highlight_per_unit: float = 0.0008
    fade_black_per_unit: float = 0.0025
    fade_white_per_unit: float = 0.0005
    clarity_per_unit: float = 0.006
    clarity_size: int = 13
    vignette_angle_per_unit: float = 0.7854 / 50
    min_step: float = 0.01


class AudioConstants(KnobConstants, frozen=True):
    neutral: Mapping[str, float] = frozendict({"volume": 1.0, "limit": 1.0})
    pitch_min: float = 0.01
    phaser_delay_per_unit: float = 0.4
    phaser_delay_ms: tuple[float, float] = (0.5, 5.0)
    room_echo_second_tap: float = 0.7
    loud_range: Mapping[str, tuple[float, float]] = frozendict(
        {
            "chorus": (30, 50),
            "flanger": (2, 10),
            "phaser": (1, 4),
            "tremolo": (2, 8),
            "vibrato": (2, 8),
        }
    )


class HandheldConstants(BaseModel, frozen=True):
    shake_bands: tuple[tuple[float, float, float], ...] = (
        (0.15, 0.45, 0.55),
        (1.2, 2.6, 0.3),
        (3.5, 6.5, 0.15),
    )
    drift_cycles: tuple[float, float] = (0.15, 0.3)
    min_seconds: float = 0.5
    overscan_spare: float = 0.01
    warp_eps: float = 1e-4


class PatternConstants(BaseModel, frozen=True):
    stripe_period: int = 12
    stripe_width: int = 6
    grid_spacing: int = 10
    grid_grey: tuple[int, int, int] = (179, 179, 179)
    check_size: int = 16
    spiral_sectors: int = 8
    particle_count: int = 300
    particle_radius: tuple[float, float] = (1.0, 4.0)
    particle_background: str = "#050505"
    particle_colours: tuple[str, ...] = (
        "#4DA3FF",
        "#FE1648",
        "#FFB020",
        "#42A6FE",
        "#F13BDE",
        "#880DFB",
    )
    psyche_colours: tuple[str, ...] = (
        "#FE1648",
        "#F13BDE",
        "#880DFB",
        "#42A6FE",
        "#FFB020",
    )


class VideoConstants(KnobConstants, frozen=True):
    neutral: Mapping[str, float] = frozendict(
        {
            "speed": 1.0,
            "zoom": 1.0,
            "corner_zoom": 1.0,
            "stretch": 1.0,
            "saturation": 1.0,
            "contrast": 1.0,
            "gamma": 1.0,
            "size": 1.0,
            "colortemp": 6500.0,
        }
    )
    min_kept_share: float = 0.5
    right_angle: float = 1.5708
    noise_min: float = 0.05
    handheld: HandheldConstants = HandheldConstants()
    patterns: PatternConstants = PatternConstants()
