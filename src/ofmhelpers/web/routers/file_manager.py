"""
ofmhelpers/web/routers/file_manager.py

Browse, download, and delete files under uploads/ and downloads/. Admin-only
-- a VA browsing to raw uploads/downloads and deleting things isn't part of
their job, so the whole router is gated via require_admin instead of
individual routes.
"""

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.auth import require_admin

router = APIRouter(
    prefix="/file-manager", tags=["file-manager"], dependencies=[Depends(require_admin)]
)

# Named roots the manager is allowed to browse -- everything else stays
# off-limits since _safe_path only resolves within whichever root is picked.
# "kieai_out" is where AI-generation output actually lands (see
# KieAIClient's out_dir); without it, finding/removing those files meant
# shelling into the server instead of using this page.
ROOTS = {
    "uploads": Path("uploads").resolve(),
    "downloads": Path("downloads").resolve(),
    "kieai_out": Path(os.getenv("OFM_KIEAI_OUT_DIR", "kieai_out")).resolve(),
}
DEFAULT_ROOT = "uploads"


def _get_root(root_name: str) -> Path:
    if root_name not in ROOTS:
        raise HTTPException(status_code=400, detail="Unknown root")
    return ROOTS[root_name]


def _safe_path(root_name: str, rel_path: str) -> Path:
    """Resolves a user-supplied relative path against the chosen root and
    refuses to leave it (blocks ../ traversal, absolute paths, symlink escape)."""
    root = _get_root(root_name)
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _list_entries(root_name: str, rel_dir: str):
    """Returns (dirs, files) for a given relative directory under the chosen root."""
    root = _get_root(root_name)
    directory = _safe_path(root_name, rel_dir)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dirs, files = [], []
    for entry in sorted(directory.iterdir()):
        rel = str(entry.relative_to(root)).replace("\\", "/")
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
def browse(request: Request, root: str = DEFAULT_ROOT, path: str = ""):
    _get_root(root).mkdir(parents=True, exist_ok=True)
    dirs, files = _list_entries(root, path)

    parent = None
    if path:
        p = Path(path)
        parent = str(p.parent).replace("\\", "/") if str(p.parent) != "." else ""

    return templates.TemplateResponse(
        request,
        "file_manager.html",
        {
            "roots": list(ROOTS.keys()),
            "current_root": root,
            "current_path": path,
            "parent": parent,
            "dirs": dirs,
            "files": files,
        },
    )


@router.get("/download")
def download(path: str, root: str = DEFAULT_ROOT):
    file_path = _safe_path(root, path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path, filename=file_path.name, media_type="application/octet-stream"
    )


@router.post("/delete")
def delete_one(path: str = Form(...), root: str = Form(DEFAULT_ROOT)):
    target = _safe_path(root, path)
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
    return RedirectResponse(
        url=f"/file-manager?root={root}&path={parent}", status_code=303
    )


@router.post("/delete-all")
def delete_all(path: str = Form(""), root: str = Form(DEFAULT_ROOT)):
    directory = _safe_path(root, path)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    return RedirectResponse(
        url=f"/file-manager?root={root}&path={path}", status_code=303
    )
