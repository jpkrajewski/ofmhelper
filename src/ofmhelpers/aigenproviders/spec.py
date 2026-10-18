"""One model of one provider, as the catalog describes it.

A model is data, not code: its id, what it outputs, and the JSON schema of
its input. Everything provider-agnostic hangs off that schema -- which fields
the form shows (`form_fields`), which take files (`media_fields`), how form
strings become typed values (`parse_form`), and whether a request is valid
before it is paid for (`validate`).

Only the subset of JSON schema the form needs is modelled (`FieldSchema`);
the raw schema is kept and is what `validate` checks against, so a keyword
not modelled here (allOf, if/then, additionalProperties) still applies.
"""

from collections.abc import Callable, Mapping
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model

from ofmhelpers.aigenproviders.errors import InvalidInputError

MediaKind = Literal["image", "video", "audio"]
OutputKind = Literal["image", "video"]
ScalarType = Literal["string", "integer", "number", "boolean"]

Payload = dict[str, JsonValue]

# Field-name words that say what a media field takes, most specific first:
# "image_reference_url" is an image, "first_frame_url" too.
_MEDIA_WORDS: tuple[tuple[str, MediaKind], ...] = (
    ("video", "video"),
    ("audio", "audio"),
    ("image", "image"),
    ("frame", "image"),
)
_SCALAR_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}
# Shown as a textarea rather than a one-line input.
_LONG_TEXT_FIELDS = frozenset({"prompt", "negative_prompt"})


class FieldSchema(BaseModel):
    """One property of an input schema. `x-media` is our own keyword: it
    names the media kind where the field name does not (kie.ai's
    `image_input`, `input_urls`)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: str | list[str] | None = None
    title: str | None = None
    description: str | None = None
    enum: list[JsonValue] | None = None
    default: JsonValue = None
    minimum: float | None = None
    maximum: float | None = None
    format: str | None = None
    min_items: int | None = Field(default=None, alias="minItems")
    max_items: int | None = Field(default=None, alias="maxItems")
    items: "FieldSchema | None" = None
    any_of: list["FieldSchema"] | None = Field(default=None, alias="anyOf")
    media: MediaKind | None = Field(default=None, alias="x-media")

    @property
    def concrete(self) -> "FieldSchema":
        """The field with a nullable `anyOf: [X, null]` unwrapped to X."""
        if not self.any_of:
            return self
        branch = next((b for b in self.any_of if b.base_type != "null"), None)
        if branch is None:
            return self
        return branch.model_copy(
            update={"title": self.title, "description": self.description}
        )

    @property
    def base_type(self) -> str | None:
        if isinstance(self.type, list):
            return next((t for t in self.type if t != "null"), None)
        return self.type

    @property
    def is_uri(self) -> bool:
        return self.format == "uri"


class MediaField(BaseModel):
    """A field filled from files the user picks: uploaded to the provider
    first, then sent as its URL (or list of URLs)."""

    name: str
    label: str
    kind: MediaKind
    multiple: bool
    required: bool
    max_items: int | None = None


class FormField(BaseModel):
    """A plain setting the form renders as an input, select or checkbox."""

    name: str
    label: str
    type: ScalarType
    required: bool
    enum: list[JsonValue] | None = None
    default: JsonValue = None
    minimum: float | None = None
    maximum: float | None = None
    description: str | None = None

    @property
    def multiline(self) -> bool:
        return self.name in _LONG_TEXT_FIELDS


def _label(name: str, field: FieldSchema) -> str:
    return field.title or name.replace("_", " ").capitalize()


def _media_kind(name: str, field: FieldSchema) -> MediaKind | None:
    if field.media:
        return field.media
    uri = field.is_uri or (field.items is not None and field.items.is_uri)
    if not uri and not name.endswith(("_url", "_urls")):
        return None
    return next((kind for word, kind in _MEDIA_WORDS if word in name), None)


class ModelSpec(BaseModel):
    """A catalog entry. `fixed` is sent on every request and never shown
    (kie.ai's `nsfw_checker: false`); `default_prompt` pre-fills the prompt
    when the model is picked."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model_id: str
    label: str
    family: str
    kind: OutputKind
    input_schema: dict[str, JsonValue]
    fixed: Payload = {}
    default_prompt: str | None = None

    @property
    def key(self) -> str:
        """What the form and a job's params name the model by."""
        return f"{self.provider}:{self.model_id}"

    @cached_property
    def properties(self) -> dict[str, FieldSchema]:
        raw = self.input_schema.get("properties")
        if not isinstance(raw, dict):
            return {}
        return {
            name: FieldSchema.model_validate(value).concrete
            for name, value in raw.items()
        }

    @cached_property
    def required(self) -> frozenset[str]:
        raw = self.input_schema.get("required")
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(str(name) for name in raw)

    @cached_property
    def media_fields(self) -> list[MediaField]:
        fields = []
        for name, field in self.properties.items():
            kind = _media_kind(name, field)
            if kind is None or field.base_type not in ("string", "array"):
                continue
            fields.append(
                MediaField(
                    name=name,
                    label=_label(name, field),
                    kind=kind,
                    multiple=field.base_type == "array",
                    required=name in self.required,
                    max_items=field.max_items,
                )
            )
        return fields

    @cached_property
    def form_fields(self) -> list[FormField]:
        """Every scalar setting. Media fields are pickers instead; URL fields
        of no media kind, arrays and objects are left out, so the provider
        applies its own default for them."""
        media = {f.name for f in self.media_fields}
        fields = []
        for name, field in self.properties.items():
            kind = field.base_type
            if name in media or kind not in _SCALAR_TYPES or field.is_uri:
                continue
            fields.append(
                FormField(
                    name=name,
                    label=_label(name, field),
                    type=kind,  # type: ignore[arg-type]
                    required=name in self.required,
                    enum=field.enum,
                    default=field.default,
                    minimum=field.minimum,
                    maximum=field.maximum,
                    description=field.description,
                )
            )
        return fields

    @cached_property
    def _form_model(self) -> type[BaseModel]:
        """The scalar settings as a Pydantic model: what turns the form's
        strings into typed values. Enum membership and ranges are left to
        `validate`, which reads them from the schema."""
        fields: dict[str, Any] = {
            f.name: (_SCALAR_TYPES[f.type] | None, None) for f in self.form_fields
        }
        return create_model(f"Form_{self.key}", **fields)

    def parse_form(self, values: Mapping[str, str]) -> Payload:
        """Typed values for the scalar settings present in `values`. A blank
        field is left out, so the provider's default applies."""
        names = {f.name for f in self.form_fields}
        present = {k: v for k, v in values.items() if k in names and v != ""}
        try:
            parsed = self._form_model.model_validate(present)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        return parsed.model_dump(exclude_none=True)

    def check(self, values: Mapping[str, Any]) -> None:
        """Raise `InvalidInputError` naming the first field that breaks the
        schema -- before anything is uploaded or paid for. Local paths stand
        in for the URLs: URL format is not checked, counts and required
        fields are."""
        body = self._body(values, str)
        error = next(
            iter(Draft202012Validator(self.input_schema).iter_errors(body)), None
        )
        if error is not None:
            where = ".".join(str(p) for p in error.absolute_path) or "input"
            msg = f"{self.label}: {where}: {error.message}"
            raise InvalidInputError(msg)

    def request(
        self, values: Mapping[str, Any], upload: Callable[[Path], str]
    ) -> Payload:
        """The body sent to the provider: `fixed` plus the settings and each
        media field's files uploaded to URLs."""
        return {**self.fixed, **self._body(values, upload)}

    def _body(
        self, values: Mapping[str, Any], upload: Callable[[Path], str]
    ) -> Payload:
        """The schema's own fields out of `values`; a media field's paths go
        through `upload`, and an empty one is left out (providers reject an
        empty list)."""
        body: Payload = {}
        media = {f.name: f for f in self.media_fields}
        for name, value in values.items():
            if name not in self.properties:
                continue
            if name not in media:
                body[name] = value
                continue
            paths = value if isinstance(value, list) else [value]
            urls: list[JsonValue] = [upload(Path(p)) for p in paths if p]
            if urls:
                body[name] = urls if media[name].multiple else urls[0]
        return body
