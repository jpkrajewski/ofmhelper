# Copyright (C) 2026 jpkrajewski

from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, create_model

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec

logger = get_logger(__name__)

PRESET_ASPECTS = ("1:1", "3:4", "4:5", "2:3", "9:16", "4:3", "3:2", "16:9")


class PictureTarget(BaseModel):
    model: str
    images: str
    aspect: str
    resolution: str
    fixed: dict[str, Any] = {}

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)

    @property
    def max_images(self) -> int:
        field = self.spec.properties[self.images]
        return field.max_items or settings.pictures.preset_max_images + 1


PICTURE_TARGETS = frozendict(
    {
        "kie": PictureTarget(
            model="kie:nano-banana-pro",
            images="image_input",
            aspect="aspect_ratio",
            resolution="resolution",
            fixed={"output_format": "jpg"},
        ),
        "runninghub": PictureTarget(
            model="runninghub:rhart-image-n-pro-official/edit",
            images="imageUrls",
            aspect="aspectRatio",
            resolution="resolution",
        ),
        "apiyi": PictureTarget(
            model="apiyi:gemini-3-pro-image",
            images="image_urls",
            aspect="aspect_ratio",
            resolution="resolution",
        ),
    },
)


def default_provider() -> str:
    wanted = settings.aigen.default_provider
    return wanted if wanted in PICTURE_TARGETS else next(iter(PICTURE_TARGETS))


def _ratio(value: str) -> float | None:
    width, _, height = value.partition(":")
    try:
        return float(width) / float(height)
    except ValueError, ZeroDivisionError:  # silent: not a ratio
        return None


def _fallback(spec: ModelSpec, field: str) -> str:
    choices = [str(v) for v in spec.properties[field].enum or []]
    return str(spec.properties[field].default or (choices or ["1:1"])[0])


def nearest_aspect(ratio: float, spec: ModelSpec, field: str) -> str:
    choices = [str(v) for v in spec.properties[field].enum or []]
    ratios = [(r, v) for v in choices if (r := _ratio(v)) is not None]
    if not ratios:
        return _fallback(spec, field)
    return min(ratios, key=lambda rv: abs(rv[0] - ratio))[1]


def aspect_of(path: str, spec: ModelSpec, field: str) -> str:
    try:
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
    except OSError, UnidentifiedImageError:
        logger.warning("picture %s unreadable: default shape", path, exc_info=True)
        return _fallback(spec, field)
    return (
        nearest_aspect(width / height, spec, field)
        if height
        else _fallback(spec, field)
    )


def run_aspect(fixed: str | None, path: str, spec: ModelSpec, field: str) -> str:
    if not fixed:
        return aspect_of(path, spec, field)
    choices = [str(v) for v in spec.properties[field].enum or []]
    if fixed in choices:
        return fixed
    ratio = _ratio(fixed)
    return nearest_aspect(ratio, spec, field) if ratio else _fallback(spec, field)


def resolution(spec: ModelSpec, field: str) -> str:
    wanted = settings.pictures.resolution
    allowed = [str(v) for v in spec.properties[field].enum or []]
    return next((v for v in allowed if v.lower() == wanted.lower()), wanted)


class ModelValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str


def _shape(target: PictureTarget) -> type[ModelValues]:
    named: dict[str, Any] = {
        target.images: (list[str], ...),
        target.aspect: (str, ...),
        target.resolution: (str, ...),
        **{name: (type(value), value) for name, value in target.fixed.items()},
    }
    return create_model("PictureValues", __base__=ModelValues, **named)


def values(target: PictureTarget, params: dict[str, Any]) -> ModelValues:
    named: dict[str, Any] = {
        target.images: params["reference_images"],
        target.aspect: params["aspect_ratio"],
        target.resolution: params["resolution"],
    }
    return _shape(target)(prompt=params["prompt"], **named)
