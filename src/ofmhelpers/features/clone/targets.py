from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    field_validator,
    model_serializer,
)

from ofmhelpers.config import WebSettings
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.utils.registry import Registry
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec

DEFAULT_RESOLUTION = "720P"


class PriceRate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    per_second: str
    usd_per_credit: str | None = None
    unit: str = "credits"
    note: str

    @field_validator("per_second", "usd_per_credit")
    @classmethod
    def _names_a_setting(cls, name: str | None) -> str | None:
        if name is not None and name not in WebSettings.model_fields:
            msg = f"No such price setting: {name}"
            raise ValueError(msg)
        return name


class CloneTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    images: str
    video: str
    audio: str
    aspect: str | None = "aspect_ratio"
    resolutions: tuple[str, ...] = ("480P", "720P", "1080P")
    max_reference_s: int = 15
    long_clone: bool = False
    price: PriceRate | None = None

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)

    def resolution_value(self, resolution: str) -> str:
        allowed = self.spec.properties["resolution"].enum or []
        return next(
            (str(v) for v in allowed if str(v).lower() == resolution.lower()),
            resolution,
        )


@lru_cache(maxsize=1)
def clone_targets() -> Registry[CloneTarget]:
    raw = Path(__file__).with_name("targets.json").read_text(encoding="utf-8")
    rows = TypeAdapter(dict[str, CloneTarget]).validate_json(raw)
    return Registry[CloneTarget](entries=rows)


def offered_resolutions(
    targets: Iterable[CloneTarget] | None = None,
) -> list[str]:
    rows = clone_targets().all() if targets is None else targets
    return list(dict.fromkeys(*row.resolutions for row in rows))


class CloneValues(BaseModel):
    target: CloneTarget
    prompt: str
    resolution: str
    duration: int
    audio: bool
    images: list[str]
    video: str

    @model_serializer
    def _in_the_models_names(self) -> dict[str, Any]:
        target = self.target
        values: dict[str, Any] = {
            "prompt": self.prompt,
            "resolution": target.resolution_value(self.resolution),
            "duration": self.duration,
            target.audio: self.audio,
            target.images: self.images,
            target.video: [self.video],
        }
        if target.aspect:
            values[target.aspect] = "adaptive"
        return values


def clone_values(
    target: CloneTarget,
    prompt: str,
    resolution: str,
    duration: int,
    audio: bool,
    images: list[str],
    video: str,
) -> CloneValues:
    return CloneValues(
        target=target,
        prompt=prompt,
        resolution=resolution,
        duration=duration,
        audio=audio,
        images=images,
        video=video,
    )
