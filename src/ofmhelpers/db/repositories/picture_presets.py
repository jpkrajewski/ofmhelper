"""/pictures presets (`picture_presets`). Nothing cached: a handful of rows,
read once per page."""

import time
from contextlib import contextmanager
lazy from collections.abc import Iterator

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from ofmhelpers.db.models import PicturePresetRow
from ofmhelpers.db.repositories.devices import DuplicateError
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.pictures import PicturePreset


def _preset(row: PicturePresetRow) -> PicturePreset:
    """Nobody's view yet: `mine` is the store's to set."""
    return PicturePreset(
        id=row.id,
        owner_id=row.user_id,
        name=row.name,
        prompt=row.prompt,
        images=list(row.images),
        aspect_ratio=row.aspect_ratio,
    )


@contextmanager
def _unique_name(name: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        msg = f"A preset named {name!r} already exists"
        raise DuplicateError(msg) from exc


class PicturePresetRepository:
    def visible(self, viewer: int, shared_by: int) -> list[PicturePreset]:
        """`viewer`'s own presets and `shared_by`'s, by name."""
        with session_scope() as s:
            rows = s.execute(
                select(PicturePresetRow)
                .where(
                    or_(
                        PicturePresetRow.user_id == viewer,
                        PicturePresetRow.user_id == shared_by,
                    )
                )
                .order_by(PicturePresetRow.name, PicturePresetRow.id)
            ).scalars()
            return [_preset(r) for r in rows]

    def get(self, preset_id: int) -> PicturePreset | None:
        with session_scope() as s:
            row = s.get(PicturePresetRow, preset_id)
            return _preset(row) if row is not None else None

    def create(
        self,
        owner: int,
        name: str,
        prompt: str,
        images: list[str],
        aspect_ratio: str | None = None,
    ) -> PicturePreset:
        now = time.time()
        row = PicturePresetRow(
            user_id=owner,
            name=name,
            prompt=prompt,
            images=images,
            aspect_ratio=aspect_ratio,
            created_at=now,
            updated_at=now,
        )
        with _unique_name(name), session_scope() as s:
            s.add(row)
            s.flush()
            return _preset(row)

    def update(
        self,
        preset_id: int,
        owner: int,
        name: str,
        prompt: str,
        images: list[str],
        aspect_ratio: str | None = None,
    ) -> PicturePreset | None:
        """None when `owner` has no such preset."""
        with _unique_name(name), session_scope() as s:
            row = s.execute(
                update(PicturePresetRow)
                .where(
                    PicturePresetRow.id == preset_id,
                    PicturePresetRow.user_id == owner,
                )
                .values(
                    name=name,
                    prompt=prompt,
                    images=images,
                    aspect_ratio=aspect_ratio,
                    updated_at=time.time(),
                )
                .returning(PicturePresetRow)
            ).scalar_one_or_none()
            return _preset(row) if row is not None else None

    def delete(self, preset_id: int, owner: int) -> bool:
        with session_scope() as s:
            gone = s.execute(
                delete(PicturePresetRow)
                .where(
                    PicturePresetRow.id == preset_id,
                    PicturePresetRow.user_id == owner,
                )
                .returning(PicturePresetRow.id)
            )
            return bool(gone.all())
