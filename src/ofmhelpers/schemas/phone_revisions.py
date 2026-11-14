"""/phones History and backup: a person's phones as they were after one
change (`phone_revisions`, written by the database's own triggers)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PhoneRevision(BaseModel):
    """One line of the History list: when, what touched the phones, and how
    much there was after it."""

    id: int
    created_at: datetime
    change: str
    phones: int
    accounts: int


class Restored(BaseModel):
    """A restore done: the revision gone back to and the logins it brought
    back (they were deleted since)."""

    revision_id: int
    logins_back: list[int] = []


class PhoneState(BaseModel):
    """One revision whole: whose phones, when, what changed, and the rows
    of `devices`, `logins` and `device_logins` after it, as stored (secrets
    still encrypted)."""

    user_id: int
    taken_at: datetime
    change: str
    snapshot: dict[str, Any]


class PhoneDump(BaseModel):
    """A backup file's content: the whole History of every owner, oldest
    first, and the newest revision it reaches."""

    taken_at: datetime
    newest: int
    states: list[PhoneState]


class DumpWritten(BaseModel):
    """A backup written: where, how many revisions of how many people.
    `path` None: nothing changed since the last file, none written."""

    path: str | None
    revisions: int = 0
    owners: int = 0


class DumpLoaded(BaseModel):
    """A backup file read back: the History revisions it added."""

    revisions: list[int]
