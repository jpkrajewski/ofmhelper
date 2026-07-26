import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image

from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    classify_kind,
    strip_asset_hash_prefix,
)

router = APIRouter(prefix="/refs", tags=["refs"])

# Reuse-picker/inputs previews only ever need a small box (see .ref-tile /
# .thumb-small CSS) -- serving the full original (multi-MB) just to paint
# ~100px is what made the picker slow to open. Cached on disk keyed by the
# same content hash already in the filename, so repeat views (or the same
# file shown in multiple pickers) never re-decode the original.
THUMBS_DIR = ASSETS_ROOT / ".thumbs"


@router.get("")
def list_refs(kind: str | None = Query(None)):
    """Lists what's already in the shared asset store -- no separate
    metadata store, this is the real files."""
    files = []
    for path in ASSETS_ROOT.glob("*"):
        if not path.is_file():
            continue
        file_kind = classify_kind(path.name)
        if kind and file_kind != kind:
            continue
        files.append(
            {
                "path": str(path),
                "name": strip_asset_hash_prefix(path.name),
                "kind": file_kind,
                "mtime": path.stat().st_mtime,
            }
        )
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files[:60]


@router.get("/file")
def get_ref_file(path: str = Query(...)):
    file_path = Path(path)
    # keep this scoped to the shared asset store
    if ASSETS_ROOT.resolve() not in file_path.resolve().parents:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type)


@router.get("/thumb")
def get_ref_thumb(path: str = Query(...), size: int = Query(200, ge=16, le=1024)):
    """Small cached preview for the reuse-picker/inputs grids -- never the
    original (see THUMBS_DIR comment). Only images have a thumbnail; video/
    audio tiles use preload="metadata"/an icon client-side and never fetch
    full bytes today, so there's nothing to save there."""
    file_path = Path(path)
    if ASSETS_ROOT.resolve() not in file_path.resolve().parents:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if classify_kind(file_path.name) != "image":
        raise HTTPException(status_code=404, detail="Not an image")

    digest = file_path.name.split("__", 1)[0]
    thumb_path = THUMBS_DIR / f"{digest}__{size}.webp"
    if not thumb_path.is_file():
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(file_path) as img:
            img.thumbnail((size, size))
            img.convert("RGB").save(thumb_path, "WEBP")

    return FileResponse(
        thumb_path,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
