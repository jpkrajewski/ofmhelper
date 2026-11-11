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
their job, so the whole router is gated via AdminOnly instead of individual
routes.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.features.files import disk, library
from ofmhelpers.features.files.disk import DEFAULT_ROOT
from ofmhelpers.features.files.library import UPLOADS, Library, Section
from ofmhelpers.web.dependencies import AdminOnly
from ofmhelpers.web.forms import FormModel
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY, see_other
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, GENERATE_TOOLS, TOOLS
from ofmhelpers.web.routers.task_helpers import media_response
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/file-manager",
    tags=["file-manager"],
    dependencies=[AdminOnly],
)

# The sections a user's runs are filed under, in tab order.
_NAMED = {
    "aigen": Section(label="AI GEN", tasks=frozenset(GENERATE_TOOLS)),
    "clone": Section(
        label="CLONE", tasks=frozenset({"clone", "clone_long", "clone_links"})
    ),
    "downloads": Section(label="DOWNLOADS", tasks=frozenset(DOWNLOAD_TOOLS)),
}
LIBRARY = Library(
    sections={
        **_NAMED,
        # Not runs: the user's part of the asset store.
        UPLOADS: Section(label="UPLOADS"),
        "other": Section(
            label="OTHER",
            tasks=frozenset(TOOLS) - {t for s in _NAMED.values() for t in s.tasks},
        ),
    },
    labels={task: tool.label for task, tool in TOOLS.items()},
)

FormText = Annotated[str, Form()]


class DiskUploadForm(FormModel):
    files: list[UploadFile]
    path: str = ""
    root: str = DEFAULT_ROOT


def _asset(user_id: int, path: str) -> Path:
    return library.asset(user_id, path)


def _disk_file(path: str, root: str = DEFAULT_ROOT) -> Path:
    return disk.file(root, path)


# A file of the user in the path's part of the asset store (`?path=`).
Asset = Annotated[Path, Depends(_asset)]
# A file under one of the disk browser's roots (`?root=&path=`).
DiskFile = Annotated[Path, Depends(_disk_file)]


def _thumb(file_path: Path) -> FileResponse:
    """An image's or video's small webp, made once and kept."""
    return FileResponse(
        library.thumb(file_path), media_type="image/webp", headers=PRIVATE_FOR_A_DAY
    )


def _attachment(found: Path, name: str) -> FileResponse:
    return FileResponse(found, filename=name, media_type="application/octet-stream")


def _back(user_id: int, section: str) -> RedirectResponse:
    query = urlencode({"user": user_id, "section": section})
    return see_other(f"/file-manager?{query}")


@router.get("")
def files_page(
    request: Request, user: int | None = None, section: str = "aigen", page: int = 0
) -> Response:
    """Users, then one user's files of one section."""
    view = LIBRARY.view(user, section, page)
    return render(request, "file_manager.html", **dict(view))


@router.get("/assets/{user_id}")
def asset_file(found: Asset, download: bool = False) -> Response:
    name = strip_asset_hash_prefix(found.name)
    return _attachment(found, name) if download else media_response(found, name)


@router.get("/assets/{user_id}/thumb")
def asset_thumb(found: Asset) -> Response:
    return _thumb(found)


@router.post("/assets/{user_id}/delete")
def asset_delete(user_id: int, found: Asset) -> Response:
    library.delete_asset(found, user_id)
    return _back(user_id, UPLOADS)


@router.post("/assets/{user_id}/upload")
def asset_upload(user_id: int, files: Annotated[list[UploadFile], File()]) -> Response:
    """Into the user's reference store (their reuse picker), deduped by
    content like any upload; media only."""
    library.add_assets(user_id, files)
    return _back(user_id, UPLOADS)


@router.get("/runs/{job_id}/{index}")
def run_file(job_id: str, index: int, download: bool = False) -> Response:
    """A run's file: in the browser, or `download=1` as an attachment."""
    found = library.run_file(job_id, index)
    return _attachment(found, found.name) if download else media_response(found)


@router.get("/runs/{job_id}/{index}/thumb")
def run_thumb(job_id: str, index: int) -> Response:
    return _thumb(library.run_file(job_id, index))


@router.post("/runs/{job_id}/{index}/delete")
def run_delete(job_id: str, index: int) -> Response:
    """The file off the disk; back to its section."""
    deleted = LIBRARY.delete_run_file(job_id, index)
    if deleted.user_id is None:
        return see_other("/file-manager")
    return _back(deleted.user_id, deleted.section)


@router.post("/upload")
def disk_upload(form: Annotated[DiskUploadForm, Form()]) -> Response:
    """Into the disk folder on screen, under the file's own (safe) name; a
    name already there is refused, never overwritten."""
    disk.upload(form.root, form.path, form.files)
    query = urlencode({"root": form.root, "path": form.path})
    return see_other(f"/file-manager/disk?{query}")


@router.get("/disk")
def browse(request: Request, root: str = DEFAULT_ROOT, path: str = "") -> Response:
    return render(request, "file_manager_disk.html", **dict(disk.folder(root, path)))


@router.get("/view")
def view(found: DiskFile) -> Response:
    """In the browser: media inline, anything else as a download (never
    same-origin HTML, `media_response`)."""
    return media_response(found)


@router.get("/thumb")
def thumb(found: DiskFile) -> Response:
    return _thumb(found)


@router.get("/download")
def download(found: DiskFile) -> Response:
    return _attachment(found, found.name)


@router.post("/delete")
def delete_one(path: FormText, root: FormText = DEFAULT_ROOT) -> Response:
    parent = disk.delete_one(root, path)
    return see_other(f"/file-manager/disk?root={root}&path={parent}")


@router.post("/delete-all")
def delete_all(path: FormText = "", root: FormText = DEFAULT_ROOT) -> Response:
    disk.delete_all(root, path)
    return see_other(f"/file-manager/disk?root={root}&path={path}")
