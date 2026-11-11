"""The file manager's raw disk browser (`/file-manager/disk`): the named
roots an admin may browse, and listing, uploading into and deleting from
them. A path never leaves its root (`safe_path`)."""

import shutil
from pathlib import Path
lazy from collections.abc import Sequence

from pydantic import BaseModel
lazy from starlette.datastructures import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import safe_filename
from ofmhelpers.features.refusals import ConflictError, NotFoundError, RefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import DirEntry, FileEntry

logger = get_logger(__name__)

DEFAULT_ROOT = "uploads"

_BYTES_PER_UNIT = 1024


class Folder(BaseModel):
    """A folder on screen; each field is a name `file_manager_disk.html`
    reads. `dirs` and `files` newest first: what just went wrong is at the
    top."""

    roots: list[str]
    current_root: str
    current_path: str
    # "Delete all" is refused at the root (`safe_path`), so not offered.
    at_root: bool
    parent: str | None
    dirs: list[DirEntry]
    files: list[FileEntry]


# Named roots the manager is allowed to browse -- everything else stays
# off-limits since safe_path only resolves within whichever root is picked.
# "kieai_out" is where AI-generation output actually lands (see
# KieAIClient's out_dir); without it, finding/removing those files meant
# shelling into the server instead of using this page.
def _roots() -> dict[str, Path]:
    """Read from the settings at each request, never kept."""
    return {
        "uploads": Path(settings.infra.uploads_root).resolve(),
        "downloads": Path("downloads").resolve(),
        "kieai_out": Path(settings.aigen.out_dir).resolve(),
    }


def _get_root(root_name: str) -> Path:
    roots = _roots()
    if root_name not in roots:
        msg = "Unknown root"
        raise RefusedError(msg)
    return roots[root_name]


def safe_path(root_name: str, rel_path: str, *, allow_root: bool = True) -> Path:
    """Resolves a user-supplied relative path against the chosen root and
    refuses to leave it (blocks ../ traversal, absolute paths, symlink escape).
    `allow_root=False` (the deletes) refuses the root itself too: deleting it,
    or everything in it, would take every user's store and every running
    job's files with it."""
    root = _get_root(root_name)
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        msg = "Invalid path"
        raise RefusedError(msg)
    if not allow_root and candidate == root:
        msg = "The root cannot be deleted"
        raise RefusedError(msg)
    return candidate


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < _BYTES_PER_UNIT:
            return f"{size:.1f} {unit}"
        size /= _BYTES_PER_UNIT
    return f"{size:.1f} TB"


def _directory(root_name: str, rel_dir: str, *, allow_root: bool = True) -> Path:
    directory = safe_path(root_name, rel_dir, allow_root=allow_root)
    if not directory.is_dir():
        msg = "Directory not found"
        raise NotFoundError(msg)
    return directory


def _parent(path: str) -> str:
    """The folder `path` is in, as the page's `path=` ("" = the root)."""
    parent = Path(path).parent
    return str(parent).replace("\\", "/") if str(parent) != "." else ""


def folder(root_name: str, rel_dir: str) -> Folder:
    root = _get_root(root_name)
    root.mkdir(parents=True, exist_ok=True)
    directory = _directory(root_name, rel_dir)
    dirs: list[DirEntry] = []
    files: list[FileEntry] = []
    for entry in directory.iterdir():
        rel = str(entry.relative_to(root)).replace("\\", "/")
        try:
            st = entry.stat()
        except OSError:
            continue  # deleted while listing, or a broken symlink
        if entry.info.is_dir():
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
    return Folder(
        roots=list(_roots()),
        current_root=root_name,
        current_path=rel_dir,
        at_root=directory == root,
        parent=_parent(rel_dir) if rel_dir else None,
        dirs=dirs,
        files=files,
    )


def file(root_name: str, rel_path: str) -> Path:
    file_path = safe_path(root_name, rel_path)
    if not file_path.is_file():
        msg = "File not found"
        raise NotFoundError(msg)
    return file_path


def upload(root_name: str, rel_dir: str, uploads: Sequence[UploadFile]) -> None:
    """Into the folder on screen, under the file's own (safe) name; a name
    already there is refused, never overwritten."""
    directory = _directory(root_name, rel_dir)
    for item in uploads:
        dest = directory / safe_filename(item.filename)
        if dest.exists():
            msg = f"{dest.name} already exists"
            raise ConflictError(msg)
        with dest.open("wb") as out:
            shutil.copyfileobj(item.file, out)
    logger.info(
        "file manager: %d upload(s) into %s/%s", len(uploads), root_name, rel_dir
    )


def delete_one(root_name: str, rel_path: str) -> str:
    """A file or a whole folder gone: the folder it was in (`path=`)."""
    target = safe_path(root_name, rel_path, allow_root=False)
    if target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    else:
        msg = "Not found"
        raise NotFoundError(msg)
    return _parent(rel_path)


def delete_all(root_name: str, rel_dir: str) -> None:
    """Everything in the folder, never the root's own content."""
    directory = _directory(root_name, rel_dir, allow_root=False)
    for entry in directory.iterdir():
        if entry.info.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
