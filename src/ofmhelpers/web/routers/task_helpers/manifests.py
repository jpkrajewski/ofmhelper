"""Reconciling a picker's manifest of new + reused files into ordered paths.

The manifest is what stops a reused reference file from being re-uploaded: the
client says "entry 2 is the file you already have at this path", and this is
the only place that claim is validated against the asset store.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from ofmhelpers.web import ref_usage
from ofmhelpers.web.routers.task_helpers.uploads import (
    classify_kind,
    in_asset_root,
    save_asset,
)


def resolve_existing_ref(raw_path: str, allowed_root: Path) -> Path:
    """Validate a path the client claims points at a previously-uploaded
    file. Raises HTTPException(400) if it's outside allowed_root or
    doesn't actually exist -- never trust a client-supplied path as-is.

    Pure: it reads the store and never writes to it. "This file was just
    picked" is recorded by the caller (`web/ref_usage.record_use`), which is
    the only place that knows a resolve meant a real reuse."""
    resolved = Path(raw_path).resolve()
    if not in_asset_root(allowed_root, resolved):
        raise HTTPException(status_code=400, detail="Invalid reference path")
    if not resolved.is_file():
        raise HTTPException(
            status_code=400, detail=f"Reference file not found: {raw_path}"
        )
    return resolved


def keep_videos(raw_paths: Sequence[str], allowed_root: Path) -> list[str]:
    """Of the `?video=` refs a page was handed (one tool sending its result
    to /clone or /clone-long), the ones that are a video still in this
    session's part of the store. A stale or forged path is dropped, never a
    400: the page just opens with fewer picked. Each kept path is absolute,
    ready to hand a file picker as an existing pick."""
    kept: list[str] = []
    for raw in raw_paths:
        try:
            resolved = resolve_existing_ref(raw, allowed_root)
        except HTTPException:
            continue
        if classify_kind(resolved.name) == "video":
            kept.append(str(resolved))
    return kept


def build_ordered_paths(
    manifest_json: str,
    new_files: Sequence[UploadFile],
    allowed_root: Path,
    *,
    user_id: int,
) -> list[str]:
    """Reconstructs an ordered list of file paths from a JSON manifest like
    [{"kind": "new"}, {"kind": "existing", "path": "..."}]. Genuinely new
    uploads go through save_asset (content-deduped); existing refs are
    resolved by path, never re-uploaded -- together this is what prevents
    duplicate files on disk. Each file counts as used by `user_id`."""
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError:
        manifest = [{"kind": "new"} for _ in new_files]

    new_files_iter = iter(new_files)
    paths: list[str] = []
    for entry in manifest:
        if entry.get("kind") == "existing":
            resolved = resolve_existing_ref(entry["path"], allowed_root)
        else:
            upload = next(new_files_iter, None)
            if upload is None or not upload.filename:
                continue
            resolved = Path(save_asset(upload, allowed_root)).resolve()
        # Picked or freshly uploaded, this file was just used, and the picker
        # opens on what you last used (see routers/refs.py's ordering). A new
        # upload counts too: save_asset dedupes by content, so re-uploading a
        # file you already had keeps its old mtime and would sink it in
        # "Recently uploaded".
        ref_usage.record_use(user_id, resolved)
        paths.append(str(resolved))
    return paths
