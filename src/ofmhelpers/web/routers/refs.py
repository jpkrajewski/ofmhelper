import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/refs", tags=["refs"])

UPLOAD_ROOT = Path("uploads")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg"}


def _kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


@router.get("")
def list_refs(kind: str | None = Query(None)):
    """Just walks the existing uploads/*-refs folders and lists what's
    already there -- no separate storage, this is the real files."""
    files = []
    for path in UPLOAD_ROOT.rglob("*"):
        if not path.is_file():
            continue
        file_kind = _kind(path)
        if kind and file_kind != kind:
            continue
        files.append(
            {
                "path": str(path),
                "name": path.name,
                "kind": file_kind,
                "mtime": path.stat().st_mtime,
            }
        )
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files[:60]


@router.get("/file")
def get_ref_file(path: str = Query(...)):
    file_path = Path(path)
    # keep this scoped to the uploads folder
    if UPLOAD_ROOT.resolve() not in file_path.resolve().parents:
        raise HTTPException(status_code=403, detail="Invalid path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type)
