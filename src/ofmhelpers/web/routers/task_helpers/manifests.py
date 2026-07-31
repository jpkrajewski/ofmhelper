"""Reconciling a picker's manifest of new + reused files into ordered paths.

The manifest is what stops a reused reference file from being re-uploaded: the
client says "entry 2 is the file you already have at this path", and this is
the only place that claim is validated against the asset store.
"""

import json
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ofmhelpers.web import ref_usage

# The module, not the constant: ASSETS_ROOT is the one seam a test moves, and
# importing the value would freeze it at import time.
from ofmhelpers.web.routers.task_helpers import uploads
from ofmhelpers.web.routers.task_helpers.uploads import save_asset
from ofmhelpers.web.schemas import ReferenceUploads


def resolve_existing_ref(raw_path: str, allowed_root: Path) -> Path:
    """Validate a path the client claims points at a previously-uploaded
    file. Raises HTTPException(400) if it's outside allowed_root or
    doesn't actually exist -- never trust a client-supplied path as-is.

    Pure: it reads the store and never writes to it. "This file was just
    picked" is recorded by the caller (`web/ref_usage.record_use`), which is
    the only place that knows a resolve meant a real reuse."""
    allowed_root = allowed_root.resolve()
    resolved = Path(raw_path).resolve()
    if allowed_root != resolved and allowed_root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid reference path")
    if not resolved.is_file():
        raise HTTPException(
            status_code=400, detail=f"Reference file not found: {raw_path}"
        )
    return resolved


def build_ordered_paths(
    manifest_json: str,
    new_files: list[UploadFile],
    allowed_root: Path,
) -> list[str]:
    """Reconstructs an ordered list of file paths from a JSON manifest like
    [{"kind": "new"}, {"kind": "existing", "path": "..."}]. Genuinely new
    uploads go through save_asset (content-deduped); existing refs are
    resolved by path, never re-uploaded -- together this is what prevents
    duplicate files on disk."""
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError:
        manifest = [{"kind": "new"} for _ in new_files]

    new_files_iter = iter(new_files)
    paths: list[str] = []
    for entry in manifest:
        if entry.get("kind") == "existing":
            resolved = resolve_existing_ref(entry["path"], allowed_root)
            # The one place a resolve means "the user picked this file", so
            # the one place that records it (see routers/refs.py's ordering).
            ref_usage.record_use(resolved)
            paths.append(str(resolved))
        else:
            upload = next(new_files_iter, None)
            if upload is None or not upload.filename:
                continue
            paths.append(save_asset(upload, allowed_root))
    return paths


def resolve_reference_uploads(refs: ReferenceUploads) -> dict[str, list[str]]:
    """The three reference pickers a generation form posts, resolved to paths
    in the shared asset store -- keyed by the picker's field name, which is
    what /generate's click-to-reuse reads back out of a job's stored params.

    Lives here rather than on `ReferenceUploads` because resolving is what
    touches the asset store; the schema only says what was posted."""
    return {
        "reference_images": build_ordered_paths(
            refs.images_manifest, refs.images, uploads.ASSETS_ROOT
        ),
        "reference_videos": build_ordered_paths(
            refs.videos_manifest, refs.videos, uploads.ASSETS_ROOT
        ),
        "reference_audio": build_ordered_paths(
            refs.audio_manifest, refs.audio, uploads.ASSETS_ROOT
        ),
    }
