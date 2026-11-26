# Copyright (C) 2026 jpkrajewski

import datetime as dt
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ofmhelpers.config.types import PlaceSample
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.values import Span

_HERE = Path(__file__).parent
_MOST_CROP = 0.5


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _ordered(span: Span, low: float) -> None:
    if not low <= span[0] <= span[1]:
        msg = f"{list(span)}: needs {low} <= lo <= hi"
        raise ValueError(msg)


class Lens(_Strict):
    label: str = ""
    make: str
    model: str
    focal_length_mm: float = Field(gt=0)
    focal_length_35mm: int = Field(gt=0)
    f_number: float = Field(gt=0)
    short_side: int = Field(ge=64)
    exposures: list[str] = Field(min_length=1)
    iso: tuple[int, int]
    quality: int = Field(ge=1, le=100)

    @field_validator("exposures")
    @classmethod
    def _fractions(cls, found: list[str]) -> list[str]:
        for text in found:
            try:
                ok = Fraction(text) > 0
            except ValueError, ZeroDivisionError:  # silent: refused below, named
                ok = False
            if not ok:
                msg = f'exposure {text!r} must be a time above 0 ("1/60")'
                raise ValueError(msg)
        return found

    @field_validator("iso")
    @classmethod
    def _iso(cls, found: tuple[int, int]) -> tuple[int, int]:
        _ordered(found, 1)
        return found


class Stills(_Strict):
    candidates: int = Field(ge=1, le=10)
    slice: Span
    hash_side: int = Field(ge=64)
    gap_s: Span

    @field_validator("slice")
    @classmethod
    def _slice(cls, found: Span) -> Span:
        if not 0 <= found[0] < found[1] <= 1:
            msg = f"{list(found)}: needs 0 <= lo < hi <= 1"
            raise ValueError(msg)
        return found

    @field_validator("gap_s")
    @classmethod
    def _gap(cls, found: Span) -> Span:
        _ordered(found, 0)
        return found


class Retouch(_Strict):
    crops: list[float] = Field(min_length=1)
    side: Span
    tone: Span
    grain: float = Field(ge=0)

    @field_validator("crops")
    @classmethod
    def _crops(cls, found: list[float]) -> list[float]:
        if not all(0 < c < _MOST_CROP for c in found):
            msg = f"{found}: every crop must be between 0 and {_MOST_CROP}"
            raise ValueError(msg)
        return found

    @field_validator("side")
    @classmethod
    def _side(cls, found: Span) -> Span:
        if not 0 < found[0] <= found[1] <= 1:
            msg = f"{list(found)}: needs 0 < lo <= hi <= 1"
            raise ValueError(msg)
        return found


class PhotoPreset(_Strict):
    label: str = ""
    device: str
    pictures: int = Field(ge=1, le=50)
    lens: Lens
    stills: Stills
    retouch: Retouch

    @model_validator(mode="after")
    def _phone(self) -> PhotoPreset:
        if load_device(self.device).recorder is None:
            msg = f"device {self.device!r} has no recorder profile (make, model, iOS)"
            raise ValueError(msg)
        return self


def photo_preset_names() -> list[str]:
    return sorted(p.stem for p in _HERE.glob("*.json"))


@lru_cache
def load_photo_preset(name_or_path: str) -> PhotoPreset:
    path = Path(name_or_path)
    if path.suffix != ".json":
        path = _HERE / f"{name_or_path}.json"
    if not path.is_file():
        known = ", ".join(photo_preset_names())
        msg = f"unknown photo preset {name_or_path!r}; known: {known}"
        raise ValueError(msg)
    return PhotoPreset.model_validate_json(path.read_text(encoding="utf-8"))


class Shot(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    taken: dt.datetime
    place: PlaceSample | None
    exposure: Fraction
    iso: int


class Photo(BaseModel, frozen=True):
    path: Path
    at: float
    crop: float
    bits: int
