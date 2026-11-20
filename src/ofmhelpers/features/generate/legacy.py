from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_serializer


class AiParams(BaseModel):
    model: str
    values: dict[str, Any]

    @model_serializer
    def _as_stored(self) -> dict[str, Any]:
        return {"model": self.model, **self.values}


class ModelPick(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_field: str
    default_variant: str
    references_field: str
    with_references: str
    without_references: str

    def key(self, template: str, params: dict[str, Any]) -> str:
        references = params.get(self.references_field)
        return template.format(
            variant=params.get(self.variant_field, self.default_variant),
            mode=self.with_references if references else self.without_references,
        )


class AsAi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    renames: dict[str, str] = {}
    pick: ModelPick | None = None

    def translate(self, params: dict[str, Any]) -> AiParams:
        key = self.pick.key(self.model, params) if self.pick else self.model
        dropped = self.pick.variant_field if self.pick else None
        return AiParams(
            model=key,
            values={
                self.renames.get(k, k): v for k, v in params.items() if k != dropped
            },
        )


class LegacyTool(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prefix: str
    label: str
    as_ai: AsAi | None = None


@lru_cache(maxsize=1)
def legacy_tools() -> MappingProxyType[str, LegacyTool]:
    raw = Path(__file__).with_name("legacy_tools.json").read_text(encoding="utf-8")
    rows = TypeAdapter(dict[str, LegacyTool]).validate_json(raw)
    return MappingProxyType(rows)
