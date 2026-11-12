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


class RestorableRef(BaseModel):
    """A missing file that was the asker's download, removed to save space:
    `POST /download-media/{job_id}/replays` fetches it again, and
    `GET /download-media/{job_id}/files/{index}/stored` then says where it
    is."""

    path: str
    job_id: str
    index: int


class MissingRefs(BaseModel):
    """`/refs/missing`: which of the asked paths can no longer be used, and
    which of those can be brought back (`restorable`)."""

    missing: list[str]
    restorable: list[RestorableRef] = []


class StoredRef(BaseModel):
    """A file the asker already has in the store, by its content digest."""

    digest: str
    path: str


class StoredRefs(BaseModel):
    """`/refs/stored`: which of the asked digests need no upload."""

    stored: list[StoredRef]


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
