"""Every model of every provider, read from each provider's `catalog.json`.

Loaded once per process: the files ship with the code and only change with a
deploy (Higgsfield's is rebuilt by `scripts/sync_higgsfield_catalog.py`).
"""

import json
from functools import lru_cache

from pydantic import TypeAdapter

from ofmhelpers.aigenproviders.base import provider_classes
from ofmhelpers.aigenproviders.spec import ModelSpec, OutputKind


@lru_cache(maxsize=1)
def catalog() -> dict[str, ModelSpec]:
    """Every model by `ModelSpec.key`, providers in discovery order and each
    provider's models in its file's order (the form lists them that way)."""
    adapter = TypeAdapter(list[ModelSpec])
    specs: dict[str, ModelSpec] = {}
    for name, cls in provider_classes().items():
        entries = json.loads(cls.catalog_path().read_text(encoding="utf-8"))
        for spec in adapter.validate_python([{**e, "provider": name} for e in entries]):
            specs[spec.key] = spec
    return specs


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
