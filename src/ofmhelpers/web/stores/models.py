"""
ofmhelpers/web/stores/models.py

Admin-managed roster of Models: a name, a profile picture, one OnlyFans
link, any number of Instagram account links (each with optional
owner/phone/SIM/password/email details) and any number of free-form
contact channels (type + value, e.g. WhatsApp/+4534343434). Postgres-backed via
web/db/ -- see routers/admin/models.py for the admin-only CRUD surface.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.web.db.repositories import ModelRepository


@lru_cache(maxsize=1)
def _repository() -> ModelRepository:
    """The process-wide model repository, built on first use rather than at
    import. Lazy because constructing it binds a Redis connection for its
    cache, and at import time OFM_REDIS_URL may not be its final value yet."""
    return ModelRepository()


def list_models() -> list[dict]:
    """Newest first."""
    return _repository().list_all()


def add_model(name: str, onlyfans_url: str) -> dict:
    return _repository().add(name, onlyfans_url)


def get_model(model_id: str) -> dict | None:
    return _repository().get(model_id)


def update_model(model_id: str, name: str, onlyfans_url: str) -> bool:
    return _repository().update(model_id, name, onlyfans_url)


def set_profile_picture(model_id: str, picture_path: str, picture_name: str) -> bool:
    return _repository().set_profile_picture(model_id, picture_path, picture_name)


def delete_model(model_id: str) -> bool:
    return _repository().delete(model_id)


def add_instagram_account(model_id: str, url: str) -> dict | None:
    return _repository().add_instagram_account(model_id, url)


def add_instagram_accounts_bulk(model_id: str, urls: list[str]) -> list[dict] | None:
    """One URL per line, blanks dropped. Returns None if the model doesn't
    exist."""
    cleaned = [u.strip() for u in urls if u.strip()]
    return _repository().add_instagram_accounts_bulk(model_id, cleaned)


def update_instagram_account(account_id: str, url: str, **details: str) -> bool:
    """`details`: owner/phone/sim_number/password/email, all optional."""
    return _repository().update_instagram_account(account_id, url, **details)


def delete_instagram_account(account_id: str) -> bool:
    return _repository().delete_instagram_account(account_id)


def add_contact(model_id: str, contact_type: str, value: str) -> dict | None:
    return _repository().add_contact(model_id, contact_type, value)


def update_contact(contact_id: str, contact_type: str, value: str) -> bool:
    return _repository().update_contact(contact_id, contact_type, value)


def delete_contact(contact_id: str) -> bool:
    return _repository().delete_contact(contact_id)


def add_competitors_bulk(model_id: str, urls: list[str]) -> list[dict] | None:
    """One competing Instagram profile URL per line, blanks dropped. Returns
    None if the model doesn't exist."""
    cleaned = [u.strip() for u in urls if u.strip()]
    return _repository().add_competitors_bulk(model_id, cleaned)


def delete_competitor(competitor_id: str) -> bool:
    return _repository().delete_competitor(competitor_id)
