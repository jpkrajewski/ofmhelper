"""A photo preset (`<name>.json` here, or any path to a `.json`; a new target
is a new file, no code) and one shot's own values. Ranges are
`repurpose.values` spans: [lo, hi], drawn per picture. A preset is checked
whole when it is loaded: a value that would fail a job is refused there,
with the field's name."""

import datetime
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ofmhelpers.config.types import PlaceSample
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.values import Span

_HERE = Path(__file__).parent
# Half of each side cut off: nothing of the picture would be left.
_MOST_CROP = 0.5


class _Strict(BaseModel):
    """Frozen, and an unknown key (a typo) is an error, not ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _ordered(span: Span, low: float) -> Span:
    if not low <= span[0] <= span[1]:
        msg = f"{list(span)}: needs {low} <= lo <= hi"
        raise ValueError(msg)
    return span


class Lens(_Strict):
    """What the camera reports for a photo, and the photo's size. `model`
    names the lens as the device's recorder profile does; the 35 mm figure
    is a photo's (the video profile's is wider: stabilisation crops)."""

    label: str = ""
    make: str
    model: str
    focal_length_mm: float = Field(gt=0)
    focal_length_35mm: int = Field(gt=0)
    f_number: float = Field(gt=0)
    # The photo's short side in px (the sensor's 16:9 crop).
    short_side: int = Field(ge=64)
    # Exposure times a shot picks from ("1/60"), and its ISO range.
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
    """How the video is looked at: `candidates` stills per picture wanted,
    each a real frame drawn inside its slice of the video (`slice`: the share
    of the slice it may come from; another seed, other frames), hashed at
    `hash_side` px; the set's shots `gap_s` seconds apart in the EXIF."""

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
        return _ordered(found, 0)


class Retouch(_Strict):
    """How a still is made another picture: cut off each side (a share
    `side` of the try's crop, its own per side), back to its size; tone
    (brightness, contrast, colour) moved by `tone`; `grain` = the noise's
    standard deviation in 8-bit levels. `crops`: the most cut off a side,
    one per try, until PDQ finds none of the video's stills nor the
    pictures already made in it."""

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
    # The phone (`repurpose/devices/<name>.json`, one with a recorder): its
    # profile's make / model / iOS, its time zone and place.
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
    """A photo preset by name (`photos/<name>.json`) or a JSON file path."""
    path = Path(name_or_path)
    if path.suffix != ".json":
        path = _HERE / f"{name_or_path}.json"
    if not path.is_file():
        known = ", ".join(photo_preset_names())
        msg = f"unknown photo preset {name_or_path!r}; known: {known}"
        raise ValueError(msg)
    return PhotoPreset.model_validate_json(path.read_text(encoding="utf-8"))


class Shot(BaseModel):
    """One photo's own values: when, where, and its exposure."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    taken: datetime.datetime
    place: PlaceSample | None
    exposure: Fraction
    iso: int


class Photo(BaseModel, frozen=True):
    """One photo written: where, from which moment of the video, the crop
    it took and how far (PDQ bits) it is from the nearest still or picture
    before it (at or under `distinct.same_picture_bits()` = it never got
    there)."""

    path: Path
    at: float
    crop: float
    bits: int
