from pydantic import BaseModel


class RefEntry(BaseModel):
    path: str
    name: str
    kind: str
    mtime: float
    used_at: float | None = None


class RestorableRef(BaseModel):
    path: str
    job_id: str
    index: int


class MissingRefs(BaseModel):
    missing: list[str]
    restorable: list[RestorableRef] = []


class StoredRef(BaseModel):
    digest: str
    path: str


class StoredRefs(BaseModel):
    stored: list[StoredRef]


class DirEntry(BaseModel):
    name: str
    rel_path: str
    mtime: float = 0


class FileEntry(DirEntry):
    size: str
    kind: str = "other"


class FileTile(BaseModel):
    name: str
    label: str
    mtime: float
    open_url: str
    delete_url: str
    thumb_url: str | None = None


class UserFiles(BaseModel):
    id: int
    label: str
    counts: dict[str, int]
