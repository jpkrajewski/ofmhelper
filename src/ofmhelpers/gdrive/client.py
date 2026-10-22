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
import threading
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httplib2
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import Error as GoogleApiError
from googleapiclient.errors import HttpError

from ofmhelpers.config import settings
from ofmhelpers.gdrive.base import DriveApi, DriveError, FolderGoneError
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
    if settings.infra.fake_externals:
        from ofmhelpers.gdrive.fake import FAKE_BOT_EMAIL

        return FAKE_BOT_EMAIL
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
    """The real Drive (`DriveApi`)."""

    def __init__(
        self,
        credentials: service_account.Credentials,
        http: Any = None,
        *,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> None:
        """`http` replaces the authorised transport (tests); `timeout_s`
        bounds each socket wait (httplib2 waits 60 s by default), `retries`
        overrides `OFM_GDRIVE_RETRIES`."""
        self._credentials = credentials
        if http is None:
            http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout_s))
        self._files = build("drive", "v3", cache_discovery=False, http=http).files()
        self._retries = settings.gdrive.retries if retries is None else retries

    def _run(self, request: HttpRequest, what: str) -> dict[str, Any]:
        try:
            return request.execute(num_retries=self._retries)
        # httplib2 errors (a DNS failure, a dropped connection) are not
        # OSError: without them they escaped as raw exceptions.
        except (
            GoogleApiError,
            GoogleAuthError,
            OSError,
            httplib2.HttpLib2Error,
        ) as exc:
            raise _failure(exc, what) from exc

    def folder(self, folder_id: str) -> DriveFolder:
        """Raises `FolderGoneError` when it is missing, trashed or not
        shared."""
        found = self._run(
            self._files.get(
                fileId=folder_id,
                fields="id, name, mimeType, trashed, parents",
                supportsAllDrives=True,
            ),
            f"Folder {folder_id}",
        )
        if found.get("trashed") or found.get("mimeType") != FOLDER_MIME:
            msg = f"Folder {folder_id} is trashed or not a folder"
            raise FolderGoneError(msg)
        return DriveFolder(
            id=found["id"], name=found["name"], parents=found.get("parents", [])
        )

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

    def file(self, file_id: str) -> DriveFile:
        """One item with its parents and preview link. Raises
        `FolderGoneError` when it is missing or not shared."""
        found = self._run(
            self._files.get(
                fileId=file_id,
                fields=f"{_FILE_FIELDS}, parents, thumbnailLink, ownedByMe, trashed",
                supportsAllDrives=True,
            ),
            f"File {file_id}",
        )
        if found.get("trashed"):
            msg = f"File {file_id} is trashed"
            raise FolderGoneError(msg)
        return DriveFile.model_validate(found)

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None:
        """`item`'s preview image (bytes, content type), `px` on its
        longest side; None while Drive has none (a video just uploaded)."""
        if not item.thumbnail_link:
            return None
        # Short-lived, and only answers with the account's token.
        url = _THUMB_SIZE.sub(f"=s{px}", item.thumbnail_link)
        try:
            response = AuthorizedSession(self._credentials).get(
                url, timeout=_THUMB_TIMEOUT_S
            )
        except (GoogleAuthError, OSError) as exc:
            raise _failure(exc, f"Preview of {item.id}") from exc
        if response.status_code != HTTPStatus.OK:
            logger.warning(
                "drive: preview of %s answered %s", item.id, response.status_code
            )
            return None
        return response.content, response.headers.get("Content-Type", "image/jpeg")


_THREAD = threading.local()


def connect(*, interactive: bool = False) -> DriveApi:
    """This thread's `Drive` (the fake one under OFM_FAKE_EXTERNALS), made
    once per thread and setting: one googleapiclient service is not
    thread-safe, and a new one per call is waste. `interactive`: a page is
    waiting, so each request gets `interactive_timeout_s` and
    `interactive_retries` -- a stalled Drive fails the page in seconds
    instead of holding its thread for minutes. Raises `DriveError` when not
    set up."""
    if settings.infra.fake_externals:
        from ofmhelpers.gdrive.fake import fake_drive

        return fake_drive()
    s = settings.gdrive
    key = _key()
    timeout_s, retries = (
        (s.interactive_timeout_s, s.interactive_retries)
        if interactive
        else (None, None)
    )
    cache_key = (key, timeout_s, retries, s.retries)
    drives: dict = _THREAD.__dict__.setdefault("drives", {})
    if cache_key not in drives:
        drives[cache_key] = Drive(
            _credentials(key), timeout_s=timeout_s, retries=retries
        )
    return drives[cache_key]
