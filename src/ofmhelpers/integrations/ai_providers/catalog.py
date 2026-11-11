"""Every model of every provider, read from each provider's `catalog.json`.

Loaded once per process: the files ship with the code and only change with a
deploy (Higgsfield's is rebuilt by `scripts/sync_higgsfield_catalog.py`).
"""

import json
from functools import lru_cache

from pydantic import TypeAdapter

from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, OutputKind
from ofmhelpers.utils.registry import Registry


@lru_cache(maxsize=1)
def catalog() -> Registry[ModelSpec]:
    """Every model by `ModelSpec.key`, providers in discovery order and each
    provider's models in its file's order (the form lists them that way)."""
    adapter = TypeAdapter(list[ModelSpec])
    specs: dict[str, ModelSpec] = {}
    for cls in provider_classes().all():
        entries = json.loads(cls.catalog_path().read_text(encoding="utf-8"))
        rows = [{**e, "provider": cls.name} for e in entries]
        for spec in adapter.validate_python(rows):
            specs[spec.key] = spec
    return Registry[ModelSpec](entries=specs)


def get_spec(key: str) -> ModelSpec:
    try:
        return catalog()[key]
    except KeyError:
        msg = f"Unknown model: {key}"
        raise KeyError(msg) from None


def models_for(provider: str, kind: OutputKind | None = None) -> list[ModelSpec]:
    return [
        spec
        for spec in catalog().all()
        if spec.provider == provider and (kind is None or spec.kind == kind)
    ]
