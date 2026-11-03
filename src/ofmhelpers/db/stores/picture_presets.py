"""/pictures presets: who sees which, and their writes.

A preset is its owner's. The agency's (the password account,
`users.agency_id()`) are shared: every user sees and runs them, only the
agency edits them. Everyone else's are their own only. The images are paths
in the owner's part of the asset store (saved by the router), so a run
reads them straight from disk and an edit never breaks an earlier job.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.db.repositories import DuplicateError, PicturePresetRepository
from ofmhelpers.db.stores.users import agency_id
from ofmhelpers.schemas.pictures import PicturePreset

__all__ = ["DuplicateError", "create", "delete", "get", "update", "visible"]


@lru_cache(maxsize=1)
def _repository() -> PicturePresetRepository:
    return PicturePresetRepository()


def _preset(row: dict, viewer: int) -> PicturePreset:
    return PicturePreset(**row, mine=row["owner_id"] == viewer)


def visible(viewer: int) -> list[PicturePreset]:
    """The viewer's own presets and the agency's, by name."""
    return [_preset(r, viewer) for r in _repository().visible(viewer, agency_id())]


def get(preset_id: int, viewer: int) -> PicturePreset | None:
    """The preset if `viewer` may see it."""
    row = _repository().get(preset_id)
    if row is None or row["owner_id"] not in (viewer, agency_id()):
        return None
    return _preset(row, viewer)


def create(
    owner: int,
    name: str,
    prompt: str,
    images: list[str],
    aspect_ratio: str | None = None,
) -> PicturePreset:
    """Raises `DuplicateError` when `owner` has a preset of that name."""
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
    """None when it is not `owner`'s; `DuplicateError` on a taken name."""
    row = _repository().update(preset_id, owner, name, prompt, images, aspect_ratio)
    return _preset(row, owner) if row is not None else None


def delete(preset_id: int, owner: int) -> bool:
    """The images stay in the asset store: past jobs still name them."""
    return _repository().delete(preset_id, owner)
