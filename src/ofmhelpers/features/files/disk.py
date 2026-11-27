# Copyright (C) 2026 jpkrajewski

import logging
import shutil
from pathlib import Path
from types import MappingProxyType
lazy from collections.abc import Sequence

from pydantic import BaseModel
lazy from starlette.datastructures import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.features.assets.store import safe_filename
from ofmhelpers.features.files.models import FileEntry
from ofmhelpers.features.files.schema import DirEntry
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.refusals import ConflictError, NotFoundError, RefusedError
lazy from ofmhelpers.features.assets.models import MediaUpload

logger = logging.getLogger(__name__)

DEFAULT_ROOT = "uploads"
KEPT_OUT_OF_UPLOADS = ("backups",)

_BYTES_PER_UNIT = 1024


class Folder(BaseModel):
    roots: list[str]
    current_root: str
    current_path: str
    at_root: bool
    parent: str | None
    dirs: list[DirEntry]
    files: list[FileEntry]


def _roots() -> MappingProxyType[str, Path]:
    return MappingProxyType(
        {
            "uploads": Path(settings.infra.uploads_root).resolve(),
            "downloads": Path("downloads").resolve(),
            "kieai_out": Path(settings.aigen.out_dir).resolve(),
        },
    )


def _get_root(root_name: str) -> Path:
    roots = _roots()
    if root_name not in roots:
        msg = "Unknown root"
        raise RefusedError(msg)
    return roots[root_name]


def safe_path(root_name: str, rel_path: str, *, allow_root: bool = True) -> Path:
    if "\0" in rel_path:
        msg = "Invalid path"
        raise RefusedError(msg)
    root = _get_root(root_name)
    try:
        candidate = (root / rel_path).resolve()
    except OSError:
        logger.warning("path %s cannot be resolved", rel_path, exc_info=True)
        msg = "Invalid path"
        raise RefusedError(msg) from None
    if not candidate.is_relative_to(root) or _kept_out(root, candidate):
        msg = "Invalid path"
        raise RefusedError(msg)
    if not allow_root and candidate == root:
        msg = "The root cannot be deleted"
        raise RefusedError(msg)
    return candidate


def _kept_out(root: Path, candidate: Path) -> bool:
    return root == _roots()["uploads"] and any(
        candidate.is_relative_to(root / name) for name in KEPT_OUT_OF_UPLOADS
    )


def within_roots(path: str | Path) -> bool:
    if "\0" in str(path):
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        logger.warning("path %s cannot be resolved", path, exc_info=True)
        return False
    return any(
        resolved.is_relative_to(root) and not _kept_out(root, resolved)
        for root in _roots().values()
    )


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
            if _kept_out(root, entry.resolve()):
                continue
            st = entry.stat()
        except FileNotFoundError:
            logger.debug("file manager: %s vanished", rel, exc_info=True)
            continue
        except OSError:
            logger.warning("file manager: %s skipped", rel, exc_info=True)
            continue
        if entry.info.is_dir():
            dirs.append(DirEntry(name=entry.name, rel_path=rel, mtime=st.st_mtime))
        else:
            files.append(
                FileEntry(
                    name=entry.name,
                    rel_path=rel,
                    mtime=st.st_mtime,
                    size=_human_size(st.st_size),
                    kind=FileKind.from_ext(entry.name),
                ),
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


def upload(
    root_name: str,
    rel_dir: str,
    uploads: Sequence[UploadFile | MediaUpload],
) -> None:
    directory = _directory(root_name, rel_dir)
    for item in uploads:
        dest = directory / safe_filename(item.filename)
        try:
            out = dest.open("xb")
        except FileExistsError:
            msg = f"{dest.name} already exists"
            raise ConflictError(msg) from None
        with out:
            shutil.copyfileobj(item.file, out)
    logger.info(
        "file manager: %d upload(s) into %s/%s",
        len(uploads),
        root_name,
        rel_dir,
    )


def delete_one(root_name: str, rel_path: str) -> str:
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
    directory = _directory(root_name, rel_dir, allow_root=False)
    for entry in directory.iterdir():
        if entry.info.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
