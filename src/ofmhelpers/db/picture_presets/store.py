from functools import lru_cache

from ofmhelpers.db.devices.repository import DuplicateError
from ofmhelpers.db.picture_presets.repository import PicturePresetRepository
from ofmhelpers.db.users.store import agency_id
lazy from ofmhelpers.db.picture_presets.schema import PicturePreset

__all__ = ["DuplicateError", "create", "delete", "get", "update", "visible"]


@lru_cache(maxsize=1)
def _repository() -> PicturePresetRepository:
    return PicturePresetRepository()


def _preset(row: PicturePreset, viewer: int) -> PicturePreset:
    return row.model_copy(update={"mine": row.owner_id == viewer})


def visible(viewer: int) -> list[PicturePreset]:
    return [_preset(r, viewer) for r in _repository().visible(viewer, agency_id())]


def get(preset_id: int, viewer: int) -> PicturePreset | None:
    row = _repository().get(preset_id)
    if row is None or row.owner_id not in (viewer, agency_id()):
        return None
    return _preset(row, viewer)


def create(
    owner: int,
    name: str,
    prompt: str,
    images: list[str],
    aspect_ratio: str | None = None,
) -> PicturePreset:
    row = _repository().create(owner, name, prompt, images, aspect_ratio)
    return _preset(row, owner)


def update(
    preset_id: int,
    owner: int,
    name: str,
    prompt: str,
    images: list[str],
    aspect_ratio: str | None = None,
) -> PicturePreset | None:
    row = _repository().update(preset_id, owner, name, prompt, images, aspect_ratio)
    return _preset(row, owner) if row is not None else None


def delete(preset_id: int, owner: int) -> bool:
    return _repository().delete(preset_id, owner)
