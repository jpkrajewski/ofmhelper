"""The asset store (`features/assets/store.py`) as routes use it: whose part
of it a request reads, and its refusals as 400s."""

from collections.abc import Callable
from functools import wraps
from pathlib import Path

from fastapi import HTTPException, Request

from ofmhelpers.features.assets import store
from ofmhelpers.features.assets.kinds import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    classify_kind,
)
from ofmhelpers.features.assets.store import (
    IMAGE_KINDS,
    MEDIA_KINDS,
    VIDEO_KINDS,
    AssetRefusedError,
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
from ofmhelpers.web.middleware import AuthMiddleware

__all__ = [
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "IMAGE_KINDS",
    "MEDIA_KINDS",
    "VIDEO_EXTS",
    "VIDEO_KINDS",
    "assets_root",
    "assets_root_for",
    "assets_root_for_actor",
    "assets_root_for_user",
    "classify_kind",
    "convert_heic_upload",
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
    """The part of the asset store this session writes into and reads from
    (`assets_root_for_user`). Request-side only: a worker job is handed the
    resolved path as a kwarg."""
    return assets_root_for_user(AuthMiddleware.user(request).id)


def refused_as_400[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    """`fn` with the store's refusal (`AssetRefusedError`) as an
    HTTPException(400) carrying its message."""

    @wraps(fn)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return fn(*args, **kwargs)
        except AssetRefusedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    return call


require_upload_kind = refused_as_400(store.require_upload_kind)
convert_heic_upload = refused_as_400(store.convert_heic_upload)
safe_filename = refused_as_400(store.safe_filename)
save_asset = refused_as_400(store.save_asset)
save_upload = refused_as_400(store.save_upload)
