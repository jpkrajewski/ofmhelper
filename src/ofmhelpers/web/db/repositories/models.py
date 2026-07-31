"""The model roster: models, their Instagram accounts, contacts and the
competitor profiles tracked against them."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from ofmhelpers.web.db.models import (
    CompetitorProfileRow,
    InstagramAccountRow,
    ModelContactRow,
    ModelRow,
)
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope

_ACCOUNT_DETAIL_FIELDS = ("owner", "phone", "sim_number", "password", "email")


def _instagram_account_to_dict(row: InstagramAccountRow) -> dict:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "url": row.url,
        **{f: getattr(row, f) for f in _ACCOUNT_DETAIL_FIELDS},
    }


def _model_contact_to_dict(row: ModelContactRow) -> dict:
    return {
        "id": row.id,
        "model_id": row.model_id,
        "type": row.type,
        "value": row.value,
    }


def _competitor_to_dict(row: CompetitorProfileRow) -> dict:
    return {"id": row.id, "model_id": row.model_id, "url": row.url}


def _model_to_dict(row: ModelRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "profile_picture_path": row.profile_picture_path,
        "profile_picture_name": row.profile_picture_name,
        "onlyfans_url": row.onlyfans_url,
        "created_at": row.created_at,
        "instagram_accounts": [
            _instagram_account_to_dict(a) for a in row.instagram_accounts
        ],
        "contacts": [_model_contact_to_dict(c) for c in row.contacts],
        "competitors": [_competitor_to_dict(c) for c in row.competitors],
    }


class ModelRepository(CachedRepository):
    cache_namespace = "model"

    @invalidates_cache
    def add(self, name: str, onlyfans_url: str) -> dict:
        row = ModelRow(
            id=uuid.uuid4().hex[:8],
            name=name,
            onlyfans_url=onlyfans_url or None,
            created_at=time.time(),
        )
        with session_scope() as s:
            s.add(row)
            s.flush()
            return _model_to_dict(row)

    @cached
    def get(self, model_id: str) -> dict | None:
        with session_scope() as s:
            row = s.get(ModelRow, model_id)
            return _model_to_dict(row) if row is not None else None

    @cached
    def list_all(self) -> list[dict]:
        """Newest first."""
        with session_scope() as s:
            rows = (
                s.execute(select(ModelRow).order_by(ModelRow.created_at.desc()))
                .scalars()
                .all()
            )
            return [_model_to_dict(r) for r in rows]

    @invalidates_cache
    def update(self, model_id: str, name: str, onlyfans_url: str) -> bool:
        with session_scope() as s:
            row = s.get(ModelRow, model_id)
            if row is None:
                return False
            row.name = name
            row.onlyfans_url = onlyfans_url or None
            return True

    @invalidates_cache
    def set_profile_picture(
        self, model_id: str, picture_path: str, picture_name: str
    ) -> bool:
        with session_scope() as s:
            row = s.get(ModelRow, model_id)
            if row is None:
                return False
            row.profile_picture_path = picture_path
            row.profile_picture_name = picture_name
            return True

    @invalidates_cache
    def delete(self, model_id: str) -> bool:
        with session_scope() as s:
            row = s.get(ModelRow, model_id)
            if row is None:
                return False
            s.delete(row)
            return True

    @invalidates_cache
    def add_instagram_account(self, model_id: str, url: str) -> dict | None:
        with session_scope() as s:
            model = s.get(ModelRow, model_id)
            if model is None:
                return None
            row = InstagramAccountRow(
                id=uuid.uuid4().hex[:8],
                model_id=model_id,
                url=url,
                created_at=time.time(),
            )
            s.add(row)
            s.flush()
            return _instagram_account_to_dict(row)

    @invalidates_cache
    def add_instagram_accounts_bulk(
        self, model_id: str, urls: list[str]
    ) -> list[dict] | None:
        """Adds many Instagram accounts at once. Returns None if the model
        doesn't exist; otherwise the newly created rows."""
        with session_scope() as s:
            model = s.get(ModelRow, model_id)
            if model is None:
                return None
            rows = [
                InstagramAccountRow(
                    id=uuid.uuid4().hex[:8],
                    model_id=model_id,
                    url=url,
                    created_at=time.time(),
                )
                for url in urls
            ]
            s.add_all(rows)
            s.flush()
            return [_instagram_account_to_dict(r) for r in rows]

    @invalidates_cache
    def update_instagram_account(
        self, account_id: str, url: str, **details: str
    ) -> bool:
        """`details` are the optional owner/phone/sim_number/password/email
        fields; the edit form posts all of them at once, so an omitted one
        means "blank", not "leave alone"."""
        with session_scope() as s:
            row = s.get(InstagramAccountRow, account_id)
            if row is None:
                return False
            row.url = url
            for field in _ACCOUNT_DETAIL_FIELDS:
                setattr(row, field, details.get(field) or None)
            return True

    @invalidates_cache
    def delete_instagram_account(self, account_id: str) -> bool:
        with session_scope() as s:
            row = s.get(InstagramAccountRow, account_id)
            if row is None:
                return False
            s.delete(row)
            return True

    @invalidates_cache
    def add_contact(self, model_id: str, contact_type: str, value: str) -> dict | None:
        with session_scope() as s:
            model = s.get(ModelRow, model_id)
            if model is None:
                return None
            row = ModelContactRow(
                id=uuid.uuid4().hex[:8],
                model_id=model_id,
                type=contact_type,
                value=value,
                created_at=time.time(),
            )
            s.add(row)
            s.flush()
            return _model_contact_to_dict(row)

    @invalidates_cache
    def update_contact(self, contact_id: str, contact_type: str, value: str) -> bool:
        with session_scope() as s:
            row = s.get(ModelContactRow, contact_id)
            if row is None:
                return False
            row.type = contact_type
            row.value = value
            return True

    @invalidates_cache
    def delete_contact(self, contact_id: str) -> bool:
        with session_scope() as s:
            row = s.get(ModelContactRow, contact_id)
            if row is None:
                return False
            s.delete(row)
            return True

    @invalidates_cache
    def add_competitors_bulk(self, model_id: str, urls: list[str]) -> list[dict] | None:
        """Competing Instagram profiles, one row per URL. None if the model
        doesn't exist."""
        with session_scope() as s:
            model = s.get(ModelRow, model_id)
            if model is None:
                return None
            rows = [
                CompetitorProfileRow(
                    id=uuid.uuid4().hex[:8],
                    model_id=model_id,
                    url=url,
                    created_at=time.time(),
                )
                for url in urls
            ]
            s.add_all(rows)
            s.flush()
            return [_competitor_to_dict(r) for r in rows]

    @invalidates_cache
    def delete_competitor(self, competitor_id: str) -> bool:
        with session_scope() as s:
            row = s.get(CompetitorProfileRow, competitor_id)
            if row is None:
                return False
            s.delete(row)
            return True
