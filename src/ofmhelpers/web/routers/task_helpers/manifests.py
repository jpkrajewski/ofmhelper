# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Sequence
lazy from pathlib import Path

lazy from starlette.datastructures import UploadFile

from ofmhelpers.features.assets import manifests
from ofmhelpers.features.assets.manifests import keep_files, keep_videos
from ofmhelpers.web.routers.task_helpers.uploads import refused_as_400

__all__ = ["build_ordered_paths", "keep_files", "keep_videos", "resolve_existing_ref"]


def resolve_existing_ref(raw_path: str, allowed_root: Path) -> Path:
    with refused_as_400():
        return manifests.resolve_existing_ref(raw_path, allowed_root)


def build_ordered_paths(
    manifest_json: str,
    new_files: Sequence[UploadFile],
    allowed_root: Path,
    *,
    user_id: int,
) -> list[str]:
    with refused_as_400():
        return manifests.build_ordered_paths(
            manifest_json,
            new_files,
            allowed_root,
            user_id=user_id,
        )
