# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel


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
