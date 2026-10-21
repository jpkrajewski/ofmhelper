"""Google Drive as the configured service account (`settings.gdrive`).

`connect()` returns a `Drive`: one googleapiclient service, so one per
thread (its httplib2 transport is not thread-safe). Every request retries
itself on 5xx / 429 / a dropped connection (`execute(num_retries=...)`).
Shared drives work too (`supportsAllDrives`). Any Drive failure surfaces as
a `DriveError` saying what went wrong, never a raw HttpError.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ofmhelpers.config import settings
from ofmhelpers.gdrive.models import FOLDER_MIME, DriveFile, DriveFolder
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from googleapiclient.http import HttpRequest

logger = get_logger(__name__)

# Full Drive: the folder is the owner's, shared to the account. The
# narrower drive.file scope sees only files the app itself created.
SCOPES = ("https://www.googleapis.com/auth/drive",)
_FILE_FIELDS = "id, name, mimeType, webViewLink, hasThumbnail, createdTime, size"
_THUMB_SIZE = re.compile(r"=s\d+$")
_THUMB_TIMEOUT_S = 20


class DriveError(Exception):
    """Drive is not set up, refused, or failed; the message says which
    (/drive shows it as is)."""


class FolderGoneError(DriveError):
    """Missing, trashed, not a folder, or not shared with the account."""


@lru_cache(maxsize=1)
def _credentials(key_json: str) -> service_account.Credentials:
    """One per key: it holds the access token and refreshes it itself."""
    try:
        return service_account.Credentials.from_service_account_info(
            json.loads(key_json), scopes=SCOPES
        )
    except (ValueError, KeyError, GoogleAuthError) as exc:
        msg = "OFM_GDRIVE_SERVICE_ACCOUNT is not a service account key (JSON)"
        raise DriveError(msg) from exc


def _key() -> str:
    key = settings.gdrive.service_account
    if key is None:
        msg = "Google Drive is not set up: OFM_GDRIVE_SERVICE_ACCOUNT is empty"
        raise DriveError(msg)
    return key.get_secret_value()


def service_account_email() -> str | None:
    """Who the root folder is shared with; None when not set up."""
    try:
        return _credentials(_key()).service_account_email
    except DriveError:
        return None


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _failure(exc: Exception, what: str) -> DriveError:
    if isinstance(exc, HttpError) and exc.resp.status == HTTPStatus.NOT_FOUND:
        return FolderGoneError(f"{what}: not found, or not shared with the bot")
    if isinstance(exc, HttpError) and exc.resp.status == HTTPStatus.FORBIDDEN:
        return DriveError(f"{what}: Drive refused ({exc.reason})")
    return DriveError(f"{what}: {exc}")


class Drive:
    def __init__(
        self, credentials: service_account.Credentials, http: Any = None
    ) -> None:
        """`http` replaces the authorised transport (tests)."""
        self._credentials = credentials
        auth = {"http": http} if http is not None else {"credentials": credentials}
        self._files = build("drive", "v3", cache_discovery=False, **auth).files()
        self._retries = settings.gdrive.retries

    def _run(self, request: HttpRequest, what: str) -> dict[str, Any]:
        try:
            return request.execute(num_retries=self._retries)
        except (HttpError, GoogleAuthError, OSError) as exc:
            raise _failure(exc, what) from exc

    def folder(self, folder_id: str) -> DriveFolder:
        """Raises `FolderGoneError` when it is missing, trashed or not
        shared."""
        found = self._run(
            self._files.get(
                fileId=folder_id,
                fields="id, name, mimeType, trashed",
                supportsAllDrives=True,
            ),
            f"Folder {folder_id}",
        )
        if found.get("trashed") or found.get("mimeType") != FOLDER_MIME:
            msg = f"Folder {folder_id} is trashed or not a folder"
            raise FolderGoneError(msg)
        return DriveFolder(id=found["id"], name=found["name"])

    def _list(self, query: str, fields: str, what: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            found = self._run(
                self._files.list(
                    q=query,
                    fields=f"nextPageToken, files({fields})",
                    pageSize=1000,
                    pageToken=page,
                    orderBy="createdTime desc",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ),
                what,
            )
            items.extend(found.get("files", []))
            page = found.get("nextPageToken")
            if not page:
                return items

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        """What is in a folder, newest first, trash left out."""
        query = f"'{_quoted(parent_id)}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"
        rows = self._list(query, _FILE_FIELDS, f"Listing folder {parent_id}")
        return [DriveFile.model_validate(row) for row in rows]

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        """The oldest folder of that name in the parent, so one made by
        hand is reused rather than duplicated."""
        query = (
            f"'{_quoted(parent_id)}' in parents and name = '{_quoted(name)}'"
            f" and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        rows = self._list(query, "id, name, createdTime", f"Finding {name!r}")
        if not rows:
            return None
        oldest = min(rows, key=lambda row: row.get("createdTime", ""))
        return DriveFolder(id=oldest["id"], name=oldest["name"])

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        made = self._run(
            self._files.create(body=body, fields="id, name", supportsAllDrives=True),
            f"Creating {name!r}",
        )
        logger.info("drive: created folder %r (%s)", made["name"], made["id"])
        return DriveFolder(id=made["id"], name=made["name"])

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        done = self._run(
            self._files.update(
                fileId=folder_id,
                body={"name": name},
                fields="id, name",
                supportsAllDrives=True,
            ),
            f"Renaming {folder_id} to {name!r}",
        )
        logger.info("drive: renamed folder %s to %r", folder_id, name)
        return DriveFolder(id=done["id"], name=done["name"])

    def thumbnail(self, file_id: str, px: int) -> tuple[bytes, str] | None:
        """The file's preview image (bytes, content type), `px` on its
        longest side; None while Drive has none (a video just uploaded)."""
        found = self._run(
            self._files.get(
                fileId=file_id, fields="thumbnailLink", supportsAllDrives=True
            ),
            f"File {file_id}",
        )
        link = found.get("thumbnailLink")
        if not link:
            return None
        # Short-lived, and only answers with the account's token.
        url = _THUMB_SIZE.sub(f"=s{px}", link)
        try:
            response = AuthorizedSession(self._credentials).get(
                url, timeout=_THUMB_TIMEOUT_S
            )
        except (GoogleAuthError, OSError) as exc:
            raise _failure(exc, f"Preview of {file_id}") from exc
        if response.status_code != HTTPStatus.OK:
            logger.warning(
                "drive: preview of %s answered %s", file_id, response.status_code
            )
            return None
        return response.content, response.headers.get("Content-Type", "image/jpeg")


def connect() -> Drive:
    """A `Drive` for this thread. Raises `DriveError` when not set up."""
    return Drive(_credentials(_key()))
