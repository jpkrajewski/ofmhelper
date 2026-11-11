"""The typed values settings hold (`settings.py`); the code they configure
(`media/image_fit.py`, `media/recorder/`) imports them from here."""

import math
import random

from pydantic import BaseModel, ConfigDict, Field


class ImageLimits(BaseModel):
    """The largest reference image a provider takes."""

    model_config = ConfigDict(frozen=True)

    max_bytes: int = Field(gt=0)
    max_side: int = Field(gt=0)

    def with_max_bytes(self, max_bytes: int | None) -> ImageLimits:
        """These limits with a model's own byte limit, when it has one."""
        if not max_bytes:
            return self
        return self.model_copy(update={"max_bytes": max_bytes})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


_M_PER_DEGREE = 111_320.0


class GeoPoint(_Frozen):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float

    @property
    def iso6709(self) -> str:
        """As an iPhone writes it: "+40.7549-073.9840+010.000/"."""
        return f"{self.latitude:+08.4f}{self.longitude:+09.4f}{self.altitude_m:+08.3f}/"

    def moved(self, north_m: float, east_m: float) -> GeoPoint:
        lon_m = _M_PER_DEGREE * math.cos(math.radians(self.latitude))
        return self.model_copy(
            update={
                "latitude": self.latitude + north_m / _M_PER_DEGREE,
                "longitude": self.longitude + east_m / lon_m,
            }
        )


class Place(_Frozen):
    """Where recordings are filmed: a centre, a radius each recording lands
    in (uniform over the disc), and the GPS accuracy range it reports."""

    center: GeoPoint
    jitter_m: float = Field(ge=0)
    accuracy_m: tuple[float, float]

    def sample(self, rng: random.Random) -> PlaceSample:
        # sqrt: uniform over the disc's area, not bunched at its centre.
        distance = self.jitter_m * math.sqrt(rng.random())
        bearing = rng.uniform(0, 2 * math.pi)
        point = self.center.moved(
            distance * math.cos(bearing), distance * math.sin(bearing)
        )
        return PlaceSample(point=point, accuracy_m=rng.uniform(*self.accuracy_m))


class PlaceSample(_Frozen):
    """Where one recording was filmed and the GPS accuracy it reports."""

    point: GeoPoint
    accuracy_m: float


class StackedKey(_Frozen):
    """One Stacked API key and the name its .env line gives it."""

    name: str
    key: str
