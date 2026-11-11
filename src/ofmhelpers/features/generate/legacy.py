"""The per-model tools /ai replaced (`legacy_tools.json`), task name -> row:
each one's old prefix and label, and how its jobs read as /ai jobs. A row with
no `as_ai` (no jobs on record) keeps prompt-only Recreate."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_serializer

from ofmhelpers.utils.registry import Registry


class AiParams(BaseModel):
    """An old job's params as /ai stores them: the catalog model, then its
    fields under the schema's names. `model_dump()` is that one flat dict."""

    model: str
    values: dict[str, Any]

    @model_serializer
    def _as_stored(self) -> dict[str, Any]:
        return {"model": self.model, **self.values}


class ModelPick(BaseModel):
    """One old tool for several catalog models, picked off the job: `{variant}`
    in `AsAi.model` is a param's value (and leaves the params), `{mode}` says
    whether references were picked."""

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
    """An old tool's params as a catalog model's fields, some under an older
    name."""

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
def legacy_tools() -> Registry[LegacyTool]:
    """The table, old task name -> tool: one for the whole process."""
    raw = Path(__file__).with_name("legacy_tools.json").read_text(encoding="utf-8")
    rows = TypeAdapter(dict[str, LegacyTool]).validate_json(raw)
    return Registry[LegacyTool](entries=rows)
