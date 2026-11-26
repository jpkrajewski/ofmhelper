# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from pathlib import Path
lazy from collections.abc import Sequence

from pydantic import BaseModel, TypeAdapter, ValidationError

from ofmhelpers.features.assets import ref_usage
from ofmhelpers.features.assets.store import in_asset_root, save_asset
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.refusals import AssetRefusedError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.assets.models import MediaUpload

logger = get_logger(__name__)


def resolve_existing_ref(raw_path: str, allowed_root: Path) -> Path:
    if "\0" in raw_path:
        msg = "Invalid reference path"
        raise AssetRefusedError(msg)
    resolved = Path(raw_path).resolve()
    if not in_asset_root(allowed_root, resolved):
        msg = "Invalid reference path"
        raise AssetRefusedError(msg)
    if not resolved.is_file():
        msg = f"Reference file not found: {raw_path}"
        raise AssetRefusedError(msg)
    return resolved


def keep_files(
    raw_paths: Sequence[str],
    allowed_root: Path,
    kind: FileKind,
) -> list[str]:
    kept: list[str] = []
    for raw in raw_paths:
        try:
            resolved = resolve_existing_ref(raw, allowed_root)
        except AssetRefusedError:  # silent: a stale path, the page offers fewer
            continue
        if FileKind.from_ext(resolved.name) == kind:
            kept.append(str(resolved))
    return kept


def keep_videos(raw_paths: Sequence[str], allowed_root: Path) -> list[str]:
    return keep_files(raw_paths, allowed_root, FileKind.VIDEO)


class ManifestEntry(BaseModel):
    kind: str = "new"
    path: str = ""


@lru_cache(maxsize=1)
def _manifest_adapter() -> TypeAdapter[list[ManifestEntry]]:
    return TypeAdapter(list[ManifestEntry])


def build_ordered_paths(
    manifest_json: str,
    new_files: Sequence[MediaUpload],
    allowed_root: Path,
    *,
    user_id: int,
) -> list[str]:
    try:
        manifest = _manifest_adapter().validate_json(manifest_json)
    except ValidationError as exc:
        logger.warning("file picker manifest unreadable", exc_info=True)
        msg = "The picked files could not be read: pick them again"
        raise AssetRefusedError(msg) from exc

    new_files_iter = iter(new_files)
    paths: list[str] = []
    for entry in manifest:
        if entry.kind == "existing":
            resolved = resolve_existing_ref(entry.path, allowed_root)
        else:
            upload = next(new_files_iter, None)
            if upload is None or not upload.filename:
                continue
            resolved = Path(save_asset(upload, allowed_root)).resolve()
        ref_usage.record_use(user_id, resolved)
        paths.append(str(resolved))
    return paths
