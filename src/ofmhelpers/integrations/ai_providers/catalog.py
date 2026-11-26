# Copyright (C) 2026 jpkrajewski

import json
from functools import lru_cache
from types import MappingProxyType

from pydantic import TypeAdapter

from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, OutputKind


@lru_cache(maxsize=1)
def catalog() -> MappingProxyType[str, ModelSpec]:
    adapter = TypeAdapter(list[ModelSpec])
    specs: dict[str, ModelSpec] = {}
    for cls in provider_classes().values():
        entries = json.loads(cls.catalog_path().read_text(encoding="utf-8"))
        rows = [{**e, "provider": cls.name} for e in entries]
        for spec in adapter.validate_python(rows):
            specs[spec.key] = spec
    return MappingProxyType(specs)


def get_spec(key: str) -> ModelSpec:
    try:
        return catalog()[key]
    except KeyError:
        msg = f"Unknown model: {key}"
        raise KeyError(msg) from None


def models_for(provider: str, kind: OutputKind | None = None) -> list[ModelSpec]:
    return [
        spec
        for spec in catalog().values()
        if spec.provider == provider and (kind is None or spec.kind == kind)
    ]
