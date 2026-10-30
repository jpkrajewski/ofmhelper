"""What /repurpose renders: every knob of `repurpose.transformations`
(read off the models, so a new knob shows up with no template edit), and
one copy's card with its checker score."""

import typing
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from ofmhelpers.repurpose.transformations.adjust import AdjustSettings
from ofmhelpers.repurpose.transformations.audio import AudioSettings
from ofmhelpers.repurpose.transformations.patterns import Pattern
from ofmhelpers.repurpose.transformations.video import VideoSettings
from ofmhelpers.web.schemas.gallery import TaskCard
from ofmhelpers.web.schemas.persistence import ResultFile


class KnobKind(StrEnum):
    RANGE = "range"  # a number or [min, max]
    NUMBER = "number"
    WORDS = "words"  # some of a few words, one picked per copy
    SWITCH = "switch"
    CHOICE = "choice"  # one of a few words


class Knob(BaseModel, frozen=True):
    # "video.zoom", "video.adjust.temp", "audio.pitch": where it goes in the preset.
    path: str
    label: str
    kind: KnobKind
    options: list[str] = []

    @property
    def dom_id(self) -> str:
        return "rp-" + self.path.replace(".", "-")


class KnobGroup(BaseModel, frozen=True):
    title: str
    knobs: list[Knob]


_WORDS: dict[str, list[str]] = {
    "look": ["border", "pattern", "none"],
    "pattern": [p.value for p in Pattern],
    "echo": ["reverb", "room"],
    "loud": ["chorus", "flanger", "phaser", "tremolo", "vibrato", "none"],
}
_CHOICES: dict[str, list[str]] = {"noise_flags": ["t", "t+u+p", "t+u", "u"]}
# Video knobs by group, in the order the transformer applies them; a knob
# not listed lands in "Other".
_VIDEO_GROUPS: dict[str, tuple[str, ...]] = {
    "Timeline": ("trim_head_s", "trim_tail_s", "cut_head", "cut_tail", "speed"),
    "Framing": (
        "mirror",
        "zoom",
        "shift_y",
        "corner_zoom",
        "stretch",
        "tilt",
        "perspective",
        "shake",
        "drift",
    ),
    "Colour": (
        "saturation",
        "contrast",
        "brightness",
        "gamma",
        "colortemp",
        "tone_mid",
        "tone_shoulder",
        "tint",
        "sharpen",
        "black_and_white",
        "vignette",
        "chroma_px",
    ),
    "Texture": (
        "noise",
        "noise_flags",
        "exposure",
        "look",
        "border",
        "pattern",
        "pattern_opacity",
    ),
    "Output": ("size", "sharpen_late", "particles"),
}
_UNITS = {"_s": " (s)", "_ms": " (ms)", "_db": " (dB)", "_px": " (px)"}


def _label(name: str) -> str:
    for suffix, unit in _UNITS.items():
        if name.endswith(suffix):
            return name.removesuffix(suffix).replace("_", " ").capitalize() + unit
    return name.replace("_", " ").capitalize()


def _flat(annotation: Any) -> set[Any]:
    """Every type inside a union / generic, origins included."""
    out = {annotation}
    origin = typing.get_origin(annotation)
    if origin is not None:
        out.add(origin)
    for arg in typing.get_args(annotation):
        out |= _flat(arg)
    return out


def _kind(name: str, annotation: Any) -> KnobKind:
    types_in = _flat(annotation)
    if tuple in types_in:
        return KnobKind.RANGE
    if list in types_in:
        return KnobKind.WORDS
    if bool in types_in:
        return KnobKind.SWITCH
    if str in types_in or name in _CHOICES:
        return KnobKind.CHOICE
    return KnobKind.NUMBER


def _knobs(model: type[BaseModel], prefix: str) -> list[Knob]:
    return [
        Knob(
            path=f"{prefix}.{name}",
            label=_label(name),
            kind=_kind(name, field.annotation),
            options=_WORDS.get(name) or _CHOICES.get(name, []),
        )
        for name, field in model.model_fields.items()
        if not _is_model(field.annotation)
    ]


def _is_model(annotation: Any) -> bool:
    return any(
        isinstance(t, type) and issubclass(t, BaseModel) for t in _flat(annotation)
    )


def knob_groups() -> list[KnobGroup]:
    video = {k.path.removeprefix("video."): k for k in _knobs(VideoSettings, "video")}
    groups = [
        KnobGroup(title=title, knobs=[video.pop(n) for n in names if n in video])
        for title, names in _VIDEO_GROUPS.items()
    ]
    if video:
        groups.append(KnobGroup(title="Other", knobs=list(video.values())))
    groups.append(
        KnobGroup(title="CapCut adjust", knobs=_knobs(AdjustSettings, "video.adjust"))
    )
    groups.append(KnobGroup(title="Audio", knobs=_knobs(AudioSettings, "audio")))
    return groups


class Medium(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class MethodScore(BaseModel, frozen=True):
    """One checker method: how much of the copy it finds in the source, 0 =
    nothing, 100 = all of it; `checker.CAUGHT_SHARE` (50) or more = caught."""

    method: str
    medium: Medium
    percent: int


class CopyScore(BaseModel, frozen=True):
    """One copy against its source (`repurpose.checker`)."""

    # Empty on copies scored before methods were shown (their extra keys
    # are ignored).
    methods: list[MethodScore] = []
    looks_like_source: bool
    sounds_like_source: bool | None

    @property
    def unique(self) -> bool:
        return not self.looks_like_source and not self.sounds_like_source


class RepurposeResult(ResultFile):
    """One copy in the job's result, with its score."""

    score: CopyScore | None = None


class RepurposeCard(TaskCard):
    """One copy's card (a done job draws one per copy), or the job's one
    card while it runs or when it failed."""

    copy_number: int = 0
    copies: int = 0
    score: CopyScore | None = None
