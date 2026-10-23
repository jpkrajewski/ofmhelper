"""Files as the pages that browse them show them: the reuse picker
(`/refs`) and the admin file manager. Field names are contract with
file-picker.js and file_manager.html."""

from pydantic import BaseModel


class RefEntry(BaseModel):
    """One stored file in the reuse picker. `used_at` is None for a file that
    was only uploaded, never picked -- the picker groups on it."""

    path: str
    name: str
    kind: str
    mtime: float
    used_at: float | None = None


class MissingRefs(BaseModel):
    """`/refs/missing`: which of the asked paths can no longer be used."""

    missing: list[str]


class DirEntry(BaseModel):
    name: str
    rel_path: str
    mtime: float = 0


class FileEntry(DirEntry):
    size: str
    # image / video get a thumbnail and open in the browser (media_kinds).
    kind: str = "other"


class FileTile(BaseModel):
    """One file on /file-manager's user view: a run's result or an upload.
    `thumb_url` only for images and videos."""

    name: str
    label: str
    mtime: float
    open_url: str
    delete_url: str
    thumb_url: str | None = None


class UserFiles(BaseModel):
    """A user's line on /file-manager: how many runs per section."""

    id: int
    label: str
    counts: dict[str, int]
