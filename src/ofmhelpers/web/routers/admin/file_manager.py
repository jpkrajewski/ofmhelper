"""
ofmhelpers/web/routers/admin/file_manager.py

Every user's files, by user then section, newest first, as big
thumbnails: open, download, delete. Only files that exist: AI GEN, CLONE,
DOWNLOADS = done runs' results still on disk; UPLOADS = the user's reference
store (their reuse picker), with upload; OTHER = other tools' results + the
raw disk browser (`/file-manager/disk`: uploads/, downloads/, kieai_out/,
with upload). The last resort when a page lost track of a result (prod,
2026-10-22: a finished run with no card buttons).
Admin-only
-- a VA browsing to raw uploads/downloads and deleting things isn't part of
their job, so the whole router is gated via AuthMiddleware.require_admin instead of
individual routes.
"""

import shutil
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.media_kinds import classify_kind
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.previews import PREVIEW_ERRORS, file_thumb
from ofmhelpers.web.routers.refs import stored_files
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, GENERATE_TOOLS, TOOLS
from ofmhelpers.web.routers.task_helpers import (
    assets_root_for_user,
    in_asset_root,
    media_response,
    safe_filename,
    save_asset,
    strip_asset_hash_prefix,
)
from ofmhelpers.web.schemas import DirEntry, FileEntry, FileTile, Job, UserFiles
from ofmhelpers.web.stores.jobs import get_job, job_counts, list_jobs_page
from ofmhelpers.web.stores.users import list_users
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(
    prefix="/file-manager",
    tags=["file-manager"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)

# Named roots the manager is allowed to browse -- everything else stays
# off-limits since _safe_path only resolves within whichever root is picked.
# "kieai_out" is where AI-generation output actually lands (see
# KieAIClient's out_dir); without it, finding/removing those files meant
# shelling into the server instead of using this page.
ROOTS = {
    "uploads": Path(settings.infra.uploads_root).resolve(),
    "downloads": Path("downloads").resolve(),
    "kieai_out": Path(settings.aigen.out_dir).resolve(),
}
DEFAULT_ROOT = "uploads"

# The sections a user's runs are filed under, in tab order: task names.
_NAMED = {
    "aigen": ("AI GEN", set(GENERATE_TOOLS)),
    "clone": ("CLONE", {"clone", "clone_long", "clone_links"}),
    "downloads": ("DOWNLOADS", set(DOWNLOAD_TOOLS)),
}
SECTIONS: dict[str, tuple[str, set[str]]] = {
    **_NAMED,
    # Not runs: the user's part of the asset store.
    "uploads": ("UPLOADS", set()),
    "other": ("OTHER", set(TOOLS) - set().union(*(t for _, t in _NAMED.values()))),
}
TILES_PER_PAGE = 60


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


_BYTES_PER_UNIT = 1024


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < _BYTES_PER_UNIT:
            return f"{size:.1f} {unit}"
        size /= _BYTES_PER_UNIT
    return f"{size:.1f} TB"


def _list_entries(
    root_name: str, rel_dir: str
) -> tuple[list[DirEntry], list[FileEntry]]:
    """(dirs, files) of a directory under the chosen root, each newest
    first: what just went wrong is at the top."""
    root = _get_root(root_name)
    directory = _safe_path(root_name, rel_dir)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dirs: list[DirEntry] = []
    files: list[FileEntry] = []
    for entry in directory.iterdir():
        rel = str(entry.relative_to(root)).replace("\\", "/")
        try:
            st = entry.stat()
        except OSError:
            continue  # deleted while listing, or a broken symlink
        if entry.is_dir():
            dirs.append(DirEntry(name=entry.name, rel_path=rel, mtime=st.st_mtime))
        else:
            files.append(
                FileEntry(
                    name=entry.name,
                    rel_path=rel,
                    mtime=st.st_mtime,
                    size=_human_size(st.st_size),
                    kind=classify_kind(entry.name),
                )
            )
    dirs.sort(key=lambda d: d.mtime, reverse=True)
    files.sort(key=lambda f: f.mtime, reverse=True)
    return dirs, files


def _section_of(task: str) -> str:
    return next(key for key, (_, tasks) in SECTIONS.items() if task in tasks)


def _uploads(user_id: int) -> dict[str, float]:
    """The user's stored files, path -> mtime (`refs.stored_files`)."""
    root = assets_root_for_user(user_id)
    return stored_files(root, None) if root.is_dir() else {}


def _users() -> list[UserFiles]:
    counts = job_counts(set(TOOLS))
    found = []
    for user in list_users():
        per_section = dict.fromkeys(SECTIONS, 0)
        for task, n in counts.get(str(user.id), {}).items():
            per_section[_section_of(task)] += n
        per_section["uploads"] = len(_uploads(user.id))
        found.append(
            UserFiles(id=user.id, label=user.name or user.email, counts=per_section)
        )
    return found


def _tile(name: str, label: str, mtime: float, url: str, query: str = "") -> FileTile:
    """`url` serves the file (`?download=1` as an attachment), `url/thumb`
    its thumbnail, `url/delete` deletes it; `query` is added to all three."""
    return FileTile(
        name=name,
        label=label,
        mtime=mtime,
        open_url=f"{url}{query}",
        delete_url=f"{url}/delete{query}",
        thumb_url=(
            f"{url}/thumb{query}" if classify_kind(name) in ("image", "video") else None
        ),
    )


def _run_tiles(job: Job) -> list[FileTile]:
    """A done run's files still on disk."""
    label = job.params.get("model") or TOOLS[job.task].label
    return [
        _tile(
            Path(path).name,
            label,
            job.finished_at or job.created_at,
            f"/file-manager/runs/{job.id}/{i}",
        )
        for i, path in enumerate(job.local_paths())
        if path and Path(path).is_file()
    ]


def _upload_tiles(user_id: int, start: int) -> tuple[list[FileTile], bool]:
    stored = sorted(_uploads(user_id).items(), key=lambda x: x[1], reverse=True)
    tiles = [
        _tile(
            strip_asset_hash_prefix(Path(path).name),
            "Upload",
            mtime,
            f"/file-manager/assets/{user_id}",
            "?" + urlencode({"path": path}),
        )
        for path, mtime in stored[start : start + TILES_PER_PAGE]
    ]
    return tiles, start + TILES_PER_PAGE < len(stored)


def _section_tiles(
    user_id: int, section: str, page: int
) -> tuple[list[FileTile], bool]:
    """One page of a user's section, newest first; whether more follow."""
    start = page * TILES_PER_PAGE
    if section == "uploads":
        return _upload_tiles(user_id, start)
    jobs, total = list_jobs_page(
        SECTIONS[section][1],
        start,
        TILES_PER_PAGE,
        actor=str(user_id),
        status="done",
    )
    return [t for j in jobs for t in _run_tiles(j)], start + TILES_PER_PAGE < total


@router.get("")
def files_page(
    request: Request, user: int | None = None, section: str = "aigen", page: int = 0
):
    """Users, then one user's files of one section."""
    if section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Unknown section")
    users = _users()
    picked = next((u for u in users if u.id == user), None)
    page = max(page, 0)
    tiles, more = _section_tiles(picked.id, section, page) if picked else ([], False)
    return render(
        request,
        "file_manager.html",
        users=users,
        picked=picked,
        sections={k: label for k, (label, _) in SECTIONS.items()},
        section=section,
        tiles=tiles,
        page=page,
        more=more,
    )


def _back(user_id: int, section: str) -> RedirectResponse:
    query = urlencode({"user": user_id, "section": section})
    return RedirectResponse(url=f"/file-manager?{query}", status_code=303)


def _asset(user_id: int, path: str) -> Path:
    """A file of `user_id`'s part of the asset store, never outside it."""
    found = Path(path)
    if not in_asset_root(assets_root_for_user(user_id), found) or not found.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return found


@router.get("/assets/{user_id}")
def asset_file(user_id: int, path: str, download: bool = False):
    found = _asset(user_id, path)
    name = strip_asset_hash_prefix(found.name)
    if download:
        return FileResponse(found, filename=name, media_type="application/octet-stream")
    return media_response(found, name)


@router.get("/assets/{user_id}/thumb")
def asset_thumb(user_id: int, path: str):
    return _thumb(_asset(user_id, path))


@router.post("/assets/{user_id}/delete")
def asset_delete(user_id: int, path: str):
    found = _asset(user_id, path)
    found.unlink()
    logger.info("file manager: deleted upload %s of user %s", found.name, user_id)
    return _back(user_id, "uploads")


@router.post("/assets/{user_id}/upload")
def asset_upload(user_id: int, files: Annotated[list[UploadFile], File()]):
    """Into the user's reference store (their reuse picker), deduped by
    content like any upload; media only (`save_asset` 400s anything else)."""
    root = assets_root_for_user(user_id)
    for upload in files:
        save_asset(upload, root)
    logger.info("file manager: %d upload(s) for user %s", len(files), user_id)
    return _back(user_id, "uploads")


def _run_file(job_id: str, index: int) -> Path:
    job = get_job(job_id)
    paths = job.local_paths() if job else []
    path = paths[index] if 0 <= index < len(paths) else None
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return Path(path)


@router.get("/runs/{job_id}/{index}")
def run_file(job_id: str, index: int, download: bool = False):
    """A run's file: in the browser, or `download=1` as an attachment."""
    found = _run_file(job_id, index)
    if download:
        return FileResponse(
            found, filename=found.name, media_type="application/octet-stream"
        )
    return media_response(found)


@router.get("/runs/{job_id}/{index}/thumb")
def run_thumb(job_id: str, index: int):
    return _thumb(_run_file(job_id, index))


@router.post("/runs/{job_id}/{index}/delete")
def run_delete(job_id: str, index: int):
    """The file off the disk; its card (and the run, when nothing is left)
    goes at the next listing (`stores.jobs._heal`). Back to its section."""
    found = _run_file(job_id, index)
    found.unlink()
    logger.info("file manager: deleted %s of job %s", found.name, job_id)
    job = get_job(job_id)
    if job is None or job.actor is None or not job.actor.isdigit():
        return RedirectResponse(url="/file-manager", status_code=303)
    return _back(int(job.actor), _section_of(job.task))


@router.post("/upload")
def disk_upload(
    files: Annotated[list[UploadFile], File()],
    path: Annotated[str, Form()] = "",
    root: Annotated[str, Form()] = DEFAULT_ROOT,
):
    """Into the disk folder on screen, under the file's own (safe) name; a
    name already there is refused, never overwritten."""
    directory = _safe_path(root, path)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    for upload in files:
        dest = directory / safe_filename(upload.filename)
        if dest.exists():
            raise HTTPException(status_code=409, detail=f"{dest.name} already exists")
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    logger.info("file manager: %d upload(s) into %s/%s", len(files), root, path)
    query = urlencode({"root": root, "path": path})
    return RedirectResponse(url=f"/file-manager/disk?{query}", status_code=303)


@router.get("/disk")
def browse(request: Request, root: str = DEFAULT_ROOT, path: str = ""):
    _get_root(root).mkdir(parents=True, exist_ok=True)
    dirs, files = _list_entries(root, path)

    parent = None
    if path:
        p = Path(path)
        parent = str(p.parent).replace("\\", "/") if str(p.parent) != "." else ""

    return render(
        request,
        "file_manager_disk.html",
        roots=list(ROOTS.keys()),
        current_root=root,
        current_path=path,
        parent=parent,
        dirs=dirs,
        files=files,
    )


def _file(root: str, path: str) -> Path:
    file_path = _safe_path(root, path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return file_path


@router.get("/view")
def view(path: str, root: str = DEFAULT_ROOT):
    """In the browser: media inline, anything else as a download (never
    same-origin HTML, `media_response`)."""
    return media_response(_file(root, path))


@router.get("/thumb")
def thumb(path: str, root: str = DEFAULT_ROOT):
    return _thumb(_file(root, path))


def _thumb(file_path: Path) -> FileResponse:
    """An image's or video's small webp, made once and kept."""
    if classify_kind(file_path.name) not in ("image", "video"):
        raise HTTPException(status_code=404, detail="No thumbnail")
    try:
        found = file_thumb(file_path)
    except PREVIEW_ERRORS:
        logger.warning("file manager: no thumbnail of %s", file_path, exc_info=True)
        raise HTTPException(status_code=404, detail="No thumbnail") from None
    return FileResponse(
        found,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/download")
def download(path: str, root: str = DEFAULT_ROOT):
    file_path = _file(root, path)
    return FileResponse(
        file_path, filename=file_path.name, media_type="application/octet-stream"
    )


@router.post("/delete")
def delete_one(
    path: Annotated[str, Form()], root: Annotated[str, Form()] = DEFAULT_ROOT
):
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
        url=f"/file-manager/disk?root={root}&path={parent}", status_code=303
    )


@router.post("/delete-all")
def delete_all(
    path: Annotated[str, Form()] = "", root: Annotated[str, Form()] = DEFAULT_ROOT
):
    directory = _safe_path(root, path)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    return RedirectResponse(
        url=f"/file-manager/disk?root={root}&path={path}", status_code=303
    )
