"""/pictures presets (`picture_presets`). Nothing cached: a handful of rows,
read once per page."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from ofmhelpers.web.db.models import PicturePresetRow
from ofmhelpers.web.db.repositories.devices import DuplicateError
from ofmhelpers.web.db.session import session_scope

if TYPE_CHECKING:
    from collections.abc import Iterator


def _row(row: PicturePresetRow) -> dict:
    return {
        "id": row.id,
        "owner_id": row.user_id,
        "name": row.name,
        "prompt": row.prompt,
        "images": list(row.images),
        "aspect_ratio": row.aspect_ratio,
        "updated_at": row.updated_at,
    }


@contextmanager
def _unique_name(name: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        msg = f"A preset named {name!r} already exists"
        raise DuplicateError(msg) from exc


class PicturePresetRepository:
    def visible(self, viewer: int, shared_by: int) -> list[dict]:
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
            return [_row(r) for r in rows]

    def get(self, preset_id: int) -> dict | None:
        with session_scope() as s:
            row = s.get(PicturePresetRow, preset_id)
            return _row(row) if row is not None else None

    def create(
        self,
        owner: int,
        name: str,
        prompt: str,
        images: list[str],
        aspect_ratio: str | None = None,
    ) -> dict:
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
            return _row(row)

    def update(
        self,
        preset_id: int,
        owner: int,
        name: str,
        prompt: str,
        images: list[str],
        aspect_ratio: str | None = None,
    ) -> dict | None:
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
            return _row(row) if row is not None else None

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
