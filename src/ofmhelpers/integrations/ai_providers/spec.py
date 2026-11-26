# Copyright (C) 2026 jpkrajewski

from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    create_model,
    model_serializer,
)

from ofmhelpers.integrations.ai_providers.errors import InvalidInputError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

MediaKind = Literal["image", "video", "audio"]
OutputKind = Literal["image", "video"]
ScalarType = Literal["string", "integer", "number", "boolean"]

Payload = dict[str, JsonValue]


class Fields(BaseModel):
    values: dict[str, Any] = {}

    @model_serializer
    def _as_given(self) -> dict[str, Any]:
        return dict(self.values)


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


class FieldSchema(BaseModel):
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
    items: FieldSchema | None = None
    any_of: list[FieldSchema] | None = Field(default=None, alias="anyOf")
    media: MediaKind | None = Field(default=None, alias="x-media")

    @property
    def concrete(self) -> FieldSchema:
        if not self.any_of:
            return self
        branch = next((b for b in self.any_of if b.base_type != "null"), None)
        if branch is None:
            return self
        return branch.model_copy(
            update={"title": self.title, "description": self.description},
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
    name: str
    label: str
    kind: MediaKind
    multiple: bool
    required: bool
    max_items: int | None = None


class FormField(BaseModel):
    name: str
    label: str
    type: ScalarType
    required: bool
    enum: list[JsonValue] | None = None
    default: JsonValue = None
    minimum: float | None = None
    maximum: float | None = None
    description: str | None = None


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
    model_config = ConfigDict(frozen=True)

    provider: str
    model_id: str
    label: str
    family: str
    kind: OutputKind
    input_schema: dict[str, JsonValue]
    fixed: Payload = {}
    default_prompt: str | None = None
    max_upload_bytes: int | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model_id}"

    @cached_property
    def properties(self) -> MappingProxyType[str, FieldSchema]:
        raw = self.input_schema.get("properties")
        schema = raw if isinstance(raw, dict) else {}
        return MappingProxyType(
            {
                name: FieldSchema.model_validate(value).concrete
                for name, value in schema.items()
            },
        )

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
                ),
            )
        return fields

    @cached_property
    def form_fields(self) -> list[FormField]:
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
                ),
            )
        return fields

    @cached_property
    def _form_model(self) -> type[BaseModel]:
        fields: dict[str, Any] = {
            f.name: (_SCALAR_TYPES[f.type] | None, None) for f in self.form_fields
        }
        return create_model(f"Form_{self.key}", **fields)

    def parse_form(self, values: Mapping[str, str]) -> Fields:
        names = {f.name for f in self.form_fields}
        present = {k: v for k, v in values.items() if k in names and v != ""}
        try:
            parsed = self._form_model.model_validate(present)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        return Fields(values=parsed.model_dump(exclude_none=True))

    def check(self, values: Mapping[str, Any]) -> None:
        body = self._body(values, str).values
        error = next(
            iter(Draft202012Validator(self.input_schema).iter_errors(body)),
            None,
        )
        if error is not None:
            where = ".".join(str(p) for p in error.absolute_path) or "input"
            msg = f"{self.label}: {where}: {error.message}"
            raise InvalidInputError(msg)

    def request(
        self,
        values: Mapping[str, Any],
        upload: Callable[[Path], str],
    ) -> Fields:
        return Fields(values={**self.fixed, **self._body(values, upload).values})

    def _body(self, values: Mapping[str, Any], upload: Callable[[Path], str]) -> Fields:
        body: dict[str, Any] = {}
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
        return Fields(values=body)
