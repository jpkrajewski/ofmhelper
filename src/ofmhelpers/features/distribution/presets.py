from pathlib import Path

from ofmhelpers.db.stores import distribution as store
from ofmhelpers.schemas.distribution import ModelPreset


def preset_of(user_id: int, key: str) -> ModelPreset | None:
    return store.repository().preset_of(user_id, key)


def presets_of(user_id: int) -> list[ModelPreset]:
    return store.repository().presets_of(user_id)


def load(user_id: int, path: Path) -> ModelPreset:
    found = ModelPreset.model_validate_json(path.read_text(encoding="utf-8")).checked()
    store.repository().save_preset(user_id, found)
    return found
