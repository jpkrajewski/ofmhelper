import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    classify_kind,
    strip_asset_hash_prefix,
)

router = APIRouter(prefix="/refs", tags=["refs"])


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
