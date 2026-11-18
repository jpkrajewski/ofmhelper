"""Model presets, private to their owner (`distribution_presets`): the repo
is public, so a preset never lives in it. Written as JSON (format and an
example: features/distribution/CLAUDE.md "Presets", `preset.example.json`),
loaded with `python -m ofmhelpers.features.distribution preset <file>
--owner <id>`; the same key again replaces it."""

from pathlib import Path

from ofmhelpers.db.stores import distribution as store
from ofmhelpers.schemas.distribution import ModelPreset


def preset_of(user_id: int, key: str) -> ModelPreset | None:
    return store.repository().preset_of(user_id, key)


def presets_of(user_id: int) -> list[ModelPreset]:
    return store.repository().presets_of(user_id)


def load(user_id: int, path: Path) -> ModelPreset:
    """The preset in `path`, checked and saved as `user_id`'s. Raises
    `ValidationError` / `ValueError` on a bad file."""
    found = ModelPreset.model_validate_json(path.read_text(encoding="utf-8")).checked()
    store.repository().save_preset(user_id, found)
    return found
