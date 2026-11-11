"""Each provider's Nano Banana Pro for /pictures (`PICTURE_TARGETS`; Higgsfield
has none, a provider joins by one entry), and a run's shape and size in the
model's own spelling."""

from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, create_model

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.catalog import get_spec
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec

# The shapes a preset may fix every run to ("" in the form: the picture's
# own); a model without one gets its nearest.
PRESET_ASPECTS = ("1:1", "3:4", "4:5", "2:3", "9:16", "4:3", "3:2", "16:9")


class PictureTarget(BaseModel):
    """One provider's Nano Banana Pro: the catalog model, its fields, and
    what every run sends besides (`fixed`: kie.ai's output format, JPEG by
    user rule 2026-11-01)."""

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
        # Gemini's own Nano Banana Pro through APIYI ($0.09 a picture, 1K-4K);
        # references inline, so no public address is needed.
        "apiyi": PictureTarget(
            model="apiyi:gemini-3-pro-image",
            images="image_urls",
            aspect="aspect_ratio",
            resolution="resolution",
        ),
    }
)


def default_provider() -> str:
    """The app's default provider when it has Nano Banana Pro, else the
    first that does."""
    wanted = settings.aigen.default_provider
    return wanted if wanted in PICTURE_TARGETS else next(iter(PICTURE_TARGETS))


def _ratio(value: str) -> float | None:
    width, _, height = value.partition(":")
    try:
        return float(width) / float(height)
    except ValueError, ZeroDivisionError:
        return None


def _fallback(spec: ModelSpec, field: str) -> str:
    choices = [str(v) for v in spec.properties[field].enum or []]
    return str(spec.properties[field].default or (choices or ["1:1"])[0])


def nearest_aspect(ratio: float, spec: ModelSpec, field: str) -> str:
    """The ratio `spec` takes nearest to `ratio` (width / height)."""
    choices = [str(v) for v in spec.properties[field].enum or []]
    ratios = [(r, v) for v in choices if (r := _ratio(v)) is not None]
    if not ratios:
        return _fallback(spec, field)
    return min(ratios, key=lambda rv: abs(rv[0] - ratio))[1]


def aspect_of(path: str, spec: ModelSpec, field: str) -> str:
    """The ratio `spec` takes nearest to the picture's own (as shown: EXIF
    rotation applied); the field's default when the picture cannot be
    read."""
    try:
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
    except OSError, UnidentifiedImageError:
        return _fallback(spec, field)
    return (
        nearest_aspect(width / height, spec, field)
        if height
        else _fallback(spec, field)
    )


def run_aspect(fixed: str | None, path: str, spec: ModelSpec, field: str) -> str:
    """A run's shape: the preset's fixed one (as the model spells it, or its
    nearest), else the picture's own."""
    if not fixed:
        return aspect_of(path, spec, field)
    choices = [str(v) for v in spec.properties[field].enum or []]
    if fixed in choices:
        return fixed
    ratio = _ratio(fixed)
    return nearest_aspect(ratio, spec, field) if ratio else _fallback(spec, field)


def resolution(spec: ModelSpec, field: str) -> str:
    """`OFM_PICTURES_RESOLUTION` in the model's spelling ("2K" / "2k")."""
    wanted = settings.pictures.resolution
    allowed = [str(v) for v in spec.properties[field].enum or []]
    return next((v for v in allowed if v.lower() == wanted.lower()), wanted)


class ModelValues(BaseModel):
    """A run's inputs under the target model's own field names. The names
    differ per provider (`image_input` / `imageUrls` / `image_urls`), so each
    target has its own shape (`_shape`); `model_dump()` is what
    `ModelSpec.check`, `ModelSpec.request` and `run_ai` take."""

    model_config = ConfigDict(extra="forbid")

    prompt: str


def _shape(target: PictureTarget) -> type[ModelValues]:
    """`ModelValues` with `target`'s fields: its reference images, shape and
    size under the model's names, and what every run sends besides."""
    named: dict[str, Any] = {
        target.images: (list[str], ...),
        target.aspect: (str, ...),
        target.resolution: (str, ...),
        **{name: (type(value), value) for name, value in target.fixed.items()},
    }
    return create_model("PictureValues", __base__=ModelValues, **named)


def values(target: PictureTarget, params: dict[str, Any]) -> ModelValues:
    """A run's provider-neutral params (a job's stored ones) as the target
    model's own fields."""
    named: dict[str, Any] = {
        target.images: params["reference_images"],
        target.aspect: params["aspect_ratio"],
        target.resolution: params["resolution"],
    }
    return _shape(target)(prompt=params["prompt"], **named)
