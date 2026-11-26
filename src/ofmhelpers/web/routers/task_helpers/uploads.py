# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
lazy from collections.abc import Iterator
lazy from pathlib import Path

from fastapi import HTTPException, Request, UploadFile

from ofmhelpers.features.assets import store
from ofmhelpers.features.assets.store import (
    assets_root,
    assets_root_for_actor,
    assets_root_for_user,
    in_asset_root,
    make_job_dir,
    register_generated_asset,
    register_grouped_results,
    strip_asset_hash_prefix,
    uploads_root,
)
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.refusals import AssetRefusedError
from ofmhelpers.web.middleware import AuthMiddleware

__all__ = [
    "assets_root",
    "assets_root_for",
    "assets_root_for_actor",
    "assets_root_for_user",
    "in_asset_root",
    "make_job_dir",
    "refused_as_400",
    "register_generated_asset",
    "register_grouped_results",
    "require_upload_kind",
    "safe_filename",
    "save_asset",
    "save_upload",
    "strip_asset_hash_prefix",
    "uploads_root",
]


def assets_root_for(request: Request) -> Path:
    return assets_root_for_user(AuthMiddleware.user(request).id)


@contextmanager
def refused_as_400() -> Iterator[None]:
    try:
        yield
    except AssetRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def require_upload_kind(name: str | None, allowed: frozenset[FileKind]) -> str:
    with refused_as_400():
        safe = store.safe_filename(name)
        store.require_upload_kind(FileKind.from_ext(safe), allowed)
        return safe


def safe_filename(name: str | None) -> str:
    with refused_as_400():
        return store.safe_filename(name)


def save_asset(upload: UploadFile, assets_root: Path | None = None) -> str:
    with refused_as_400():
        return store.save_asset(upload, assets_root)


def save_upload(job_dir: Path, upload: UploadFile) -> str:
    with refused_as_400():
        return store.save_upload(job_dir, upload)
