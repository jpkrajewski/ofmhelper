"""The Wan 3.0 table (`targets.json`), read by /clone, /clone-links and
/clone-long: per provider, its reference-to-video model, which of that model's
fields take the subject, the video and the audio switch, the resolutions
offered, the longest reference video, and where its price lives. A provider
joins by one row. A job's params stay provider-neutral (`reference_images`,
`reference_videos`, `resolution` ...), so the pages and Recreate read every run
the same way."""

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
    """Where a provider's price lives. `per_second` and `usd_per_credit` name
    `settings.web` fields, so prices stay env-tunable: `unit`s per second of
    source video by resolution, and what one costs (None: `per_second` is in
    dollars already). `unit`: what the provider bills in."""

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
    """One provider's Wan 3.0 reference-to-video: the catalog model, and the
    fields of its schema the subject, the video and the audio switch go in,
    and the longest reference video it takes: Wan 3.0 caps the input at
    15 s on every provider (kie.ai answers a longer one with a 422)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    images: str
    video: str
    audio: str
    # Sent "adaptive" (the shape follows the video); None = left out, for a
    # provider that has no "adaptive" and picks the shape from the inputs
    # itself (Elyum).
    aspect: str | None = "aspect_ratio"
    resolutions: tuple[str, ...] = ("480P", "720P", "1080P")
    max_reference_s: int = 15
    # On /clone-long, which sends every piece one ratio in `aspect_ratio`.
    long_clone: bool = False
    # None: no estimate (the panel hides).
    price: PriceRate | None = None

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)

    def resolution_value(self, resolution: str) -> str:
        """`resolution` in the spelling the model takes ("720P" vs "720p")."""
        allowed = self.spec.properties["resolution"].enum or []
        return next(
            (str(v) for v in allowed if str(v).lower() == resolution.lower()),
            resolution,
        )


@lru_cache(maxsize=1)
def clone_targets() -> Registry[CloneTarget]:
    """The table, provider -> row: one for the whole process."""
    raw = Path(__file__).with_name("targets.json").read_text(encoding="utf-8")
    rows = TypeAdapter(dict[str, CloneTarget]).validate_json(raw)
    return Registry[CloneTarget](entries=rows)


def offered_resolutions(
    targets: Iterable[CloneTarget] | None = None,
) -> list[str]:
    """What a form offers: every resolution of `targets` (default: the whole
    table), in the table's order."""
    rows = clone_targets().all() if targets is None else targets
    return list(dict.fromkeys(*row.resolutions for row in rows))


class CloneValues(BaseModel):
    """One run's inputs. Its dump (`model_dump()`) is what the provider's
    model takes: the same values under the target model's own field names."""

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
    """One run's inputs for `target`'s model."""
    return CloneValues(
        target=target,
        prompt=prompt,
        resolution=resolution,
        duration=duration,
        audio=audio,
        images=images,
        video=video,
    )
