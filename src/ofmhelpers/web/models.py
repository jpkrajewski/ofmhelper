"""
ofmhelpers/web/models.py

Admin-managed roster of Models: a name, a profile picture, one OnlyFans
link, and any number of Instagram account links. Postgres-backed via
web/db/ -- see routers/models.py for the admin-only CRUD surface.
"""

from __future__ import annotations

from ofmhelpers.web.db.repository import ModelRepository

_repo = ModelRepository()


def list_models() -> list[dict]:
    """Newest first."""
    return _repo.list_all()


def add_model(name: str, onlyfans_url: str) -> dict:
    return _repo.add(name, onlyfans_url)


def get_model(model_id: str) -> dict | None:
    return _repo.get(model_id)


def update_model(model_id: str, name: str, onlyfans_url: str) -> bool:
    return _repo.update(model_id, name, onlyfans_url)


def set_profile_picture(model_id: str, picture_path: str, picture_name: str) -> bool:
    return _repo.set_profile_picture(model_id, picture_path, picture_name)


def delete_model(model_id: str) -> bool:
    return _repo.delete(model_id)


def add_instagram_account(model_id: str, url: str) -> dict | None:
    return _repo.add_instagram_account(model_id, url)


def add_instagram_accounts_bulk(model_id: str, urls: list[str]) -> list[dict] | None:
    """One URL per line, blanks dropped. Returns None if the model doesn't
    exist."""
    cleaned = [u.strip() for u in urls if u.strip()]
    return _repo.add_instagram_accounts_bulk(model_id, cleaned)


def update_instagram_account(account_id: str, url: str) -> bool:
    return _repo.update_instagram_account(account_id, url)


def delete_instagram_account(account_id: str) -> bool:
    return _repo.delete_instagram_account(account_id)
