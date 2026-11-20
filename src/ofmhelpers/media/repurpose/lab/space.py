from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from ofmhelpers.media.repurpose.presets import Preset
from ofmhelpers.media.repurpose.transformations.patterns import Pattern

OVER_CAP = 1.5


class Part(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class Role(StrEnum):
    FRAMING = "framing"
    LOOK = "look"


class Shape(StrEnum):
    SYM = "sym"
    AROUND_ONE = "around_one"
    UP = "up"
    POS = "pos"
    DOWN = "down"
    NEG = "neg"
    AROUND = "around"


class Knob(BaseModel, frozen=True):
    name: str
    field: str
    part: Part
    role: Role
    shape: Shape
    cap: float
    base: float = 0.0
    integer: bool = False
    extra: dict[str, Any] = {}

    def value(self, strength: float) -> list[float]:
        s = strength
        lo, hi = {
            Shape.SYM: (-s, s),
            Shape.AROUND_ONE: (1 - s, 1 + s),
            Shape.UP: (1 + s / 2, 1 + s),
            Shape.POS: (s / 2, s),
            Shape.DOWN: (1 - s, 1 - s / 2),
            Shape.NEG: (-s, -s / 2),
            Shape.AROUND: (self.base - s, self.base + s),
        }[self.shape]
        if self.integer:
            lo, hi = max(1, round(lo)), max(1, round(hi))
        return [round(lo, 5), round(hi, 5)]


def _v(name: str, role: Role, shape: Shape, cap: float, **kw: Any) -> Knob:
    return Knob(
        name=name,
        field=kw.pop("field", name),
        part=Part.VIDEO,
        role=role,
        shape=shape,
        cap=cap,
        **kw,
    )


def _a(name: str, shape: Shape, cap: float, **kw: Any) -> Knob:
    return Knob(
        name=name,
        field=kw.pop("field", name),
        part=Part.AUDIO,
        role=Role.LOOK,
        shape=shape,
        cap=cap,
        **kw,
    )


F, L = Role.FRAMING, Role.LOOK
_PATTERNS = [p.value for p in Pattern]

KNOBS: list[Knob] = [
    _v("speed", F, Shape.AROUND_ONE, 0.06),
    _v("trim_head_s", F, Shape.POS, 0.5),
    _v("cut_head", F, Shape.POS, 0.07),
    _v("cut_tail", F, Shape.POS, 0.07),
    _v("zoom", F, Shape.UP, 0.06),
    _v("shift_y", F, Shape.SYM, 0.03),
    _v("corner_zoom", F, Shape.UP, 0.06),
    _v("stretch", F, Shape.UP, 0.03),
    _v("tilt", F, Shape.SYM, 1.0),
    _v("perspective", F, Shape.POS, 0.03),
    _v("shake", F, Shape.POS, 0.006),
    _v("drift", F, Shape.POS, 0.045),
    _v("size", F, Shape.DOWN, 0.05),
    _v("saturation", L, Shape.AROUND_ONE, 0.15),
    _v("contrast", L, Shape.AROUND_ONE, 0.1),
    _v("brightness", L, Shape.SYM, 0.05),
    _v("gamma", L, Shape.AROUND_ONE, 0.04),
    _v("colortemp", L, Shape.AROUND, 700.0, base=6500.0),
    _v("tone_mid", L, Shape.SYM, 10.0),
    _v("tone_shoulder", L, Shape.SYM, 6.0),
    _v("tint", L, Shape.SYM, 0.06),
    _v("sharpen", L, Shape.POS, 1.2),
    _v("vignette", L, Shape.POS, 0.4),
    _v("chroma_px", L, Shape.POS, 2.0, integer=True),
    _v("noise", L, Shape.POS, 10.0),
    _v("exposure", L, Shape.SYM, 0.15),
    _v("border", L, Shape.POS, 0.03, extra={"look": ["border"]}),
    _v(
        "pattern",
        L,
        Shape.POS,
        0.06,
        field="pattern_opacity",
        extra={"look": ["pattern"], "pattern": _PATTERNS},
    ),
    _v("sharpen_late", L, Shape.POS, 0.4),
    _v("particles", L, Shape.POS, 12.0),
    _a("volume", Shape.AROUND_ONE, 0.06),
    _a("pitch", Shape.SYM, 0.5),
    _a("phaser_shift", Shape.POS, 10.0),
    _a("treble_db", Shape.POS, 4.0),
    _a("bass_cut_db", Shape.POS, 3.0),
    _a("mid_db", Shape.SYM, 3.0),
    _a(
        "reverb",
        Shape.POS,
        0.45,
        field="reverb_decay",
        extra={"echo": ["reverb"], "reverb_ms": [40.0, 90.0]},
    ),
    _a("room", Shape.POS, 50.0, field="room_ms", extra={"echo": ["room"]}),
    *[
        _a(f"loud_{e}", Shape.POS, 1.0, field="loudness", extra={"loud": [e]})
        for e in ("chorus", "flanger", "phaser", "tremolo", "vibrato")
    ],
    _a("compress_db", Shape.NEG, 24.0),
    _a("deess_db", Shape.POS, 3.0),
    _a("limit", Shape.DOWN, 0.12),
]
BY_NAME = {k.name: k for k in KNOBS}
_AUDIO_BASE: dict[str, Any] = {"match_level": True}


class _Settings(BaseModel, extra="allow"):
    pass


def knobs(part: Part) -> list[Knob]:
    return [k for k in KNOBS if k.part is part]


def _settings(strengths: dict[str, float], part: Part) -> _Settings:
    out: dict[str, Any] = dict(_AUDIO_BASE) if part is Part.AUDIO else {}
    for name, strength in strengths.items():
        knob = BY_NAME[name]
        if knob.part is not part or strength <= 0:
            continue
        if knob.field == "loudness":
            out[knob.field] = max(out.get("loudness", 0.0), round(strength, 3))
        else:
            out[knob.field] = knob.value(strength)
        for key, extra in knob.extra.items():
            if isinstance(extra, list):
                out[key] = sorted({*out.get(key, []), *extra})
            else:
                out[key] = extra
    return _Settings(**out)


def preset(strengths: dict[str, float], label: str = "") -> Preset:
    return Preset.model_validate(
        {
            "label": label,
            "video": _settings(strengths, Part.VIDEO).model_dump(),
            "audio": _settings(strengths, Part.AUDIO).model_dump(),
        }
    )
