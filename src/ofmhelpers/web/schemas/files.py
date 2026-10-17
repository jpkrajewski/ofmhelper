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


class DirEntry(BaseModel):
    name: str
    rel_path: str


class FileEntry(DirEntry):
    size: str
