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
