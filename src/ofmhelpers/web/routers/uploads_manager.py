"""
ofmhelpers/web/routers/uploads_manager.py

Browse, download, and delete files under the uploads/ root.
"""

import shutil
from pathlib import Path

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/uploads-manager", tags=["uploads-manager"])

UPLOADS_ROOT = Path("uploads").resolve()


def _safe_path(rel_path: str) -> Path:
    """Resolves a user-supplied relative path against UPLOADS_ROOT and
    refuses to leave it (blocks ../ traversal, absolute paths, symlink escape)."""
    candidate = (UPLOADS_ROOT / rel_path).resolve()
    if not candidate.is_relative_to(UPLOADS_ROOT):
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _list_entries(rel_dir: str):
    """Returns (dirs, files) for a given relative directory under uploads/."""
    directory = _safe_path(rel_dir)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dirs, files = [], []
    for entry in sorted(directory.iterdir()):
        rel = str(entry.relative_to(UPLOADS_ROOT)).replace("\\", "/")
        if entry.is_dir():
            dirs.append({"name": entry.name, "rel_path": rel})
        else:
            stat = entry.stat()
            files.append(
                {
                    "name": entry.name,
                    "rel_path": rel,
                    "size": _human_size(stat.st_size),
                }
            )
    return dirs, files


@router.get("")
def browse(request: Request, path: str = ""):
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    dirs, files = _list_entries(path)

    parent = None
    if path:
        p = Path(path)
        parent = str(p.parent).replace("\\", "/") if str(p.parent) != "." else ""

    return templates.TemplateResponse(
        request,
        "uploads_manager.html",
        {"current_path": path, "parent": parent, "dirs": dirs, "files": files},
    )


@router.get("/download")
def download(path: str):
    file_path = _safe_path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path, filename=file_path.name, media_type="application/octet-stream"
    )


@router.post("/delete")
def delete_one(path: str = Form(...)):
    target = _safe_path(path)
    if target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        raise HTTPException(status_code=404, detail="Not found")

    parent = (
        str(Path(path).parent).replace("\\", "/")
        if str(Path(path).parent) != "."
        else ""
    )
    return RedirectResponse(url=f"/uploads-manager?path={parent}", status_code=303)


@router.post("/delete-all")
def delete_all(path: str = Form("")):
    directory = _safe_path(path)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    return RedirectResponse(url=f"/uploads-manager?path={path}", status_code=303)
