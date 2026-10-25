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
from googleapiclient.http import MediaFileUpload

from ofmhelpers.config import settings
from ofmhelpers.gdrive.base import DriveApi, DriveError, FolderGoneError
from ofmhelpers.gdrive.models import FOLDER_MIME, DriveFile, DriveFolder, DriveStorage
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from google.auth.credentials import Credentials
    from googleapiclient.http import HttpRequest

logger = get_logger(__name__)


def read_live_mode() -> bool:
    """Dev-only live-read: on when OFM_DRIVE_READ_LIVE is set and this is not
    production. Always off in production -- the live folders are never served
    through the read-only guard there."""
    return settings.gdrive.read_live and not settings.infra.production


_READ_ONLY = "read-live mode (OFM_DRIVE_READ_LIVE): writing to the live Drive is off"


class ReadOnlyDrive:
    """A Drive whose reads pass through to the real one and whose every write
    raises `DriveError`: dev reads the live folders to debug prod without any
    chance of changing them (`read_live_mode`)."""

    def __init__(self, inner: DriveApi) -> None:
        self._inner = inner

    def folder(self, folder_id: str) -> DriveFolder:
        return self._inner.folder(folder_id)

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        return self._inner.children(parent_id, folders_only=folders_only)

    def children_of(self, parent_ids: list[str]) -> dict[str, list[DriveFile]]:
        return self._inner.children_of(parent_ids)

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        return self._inner.find_folder(parent_id, name)

    def file(self, file_id: str) -> DriveFile:
        return self._inner.file(file_id)

    def thumbnail(self, item: DriveFile, px: int) -> tuple[bytes, str] | None:
        return self._inner.thumbnail(item, px)

    def storage(self) -> DriveStorage:
        return self._inner.storage()

    # Every write raises -- the whole point of the guard.
    def create_folder(self, *args: object, **kwargs: object) -> DriveFolder:
        raise DriveError(_READ_ONLY)

    def ensure_folder(self, *args: object, **kwargs: object) -> DriveFolder:
        raise DriveError(_READ_ONLY)

    def rename(self, *args: object, **kwargs: object) -> DriveFolder:
        raise DriveError(_READ_ONLY)

    def upload(self, *args: object, **kwargs: object) -> DriveFile:
        raise DriveError(_READ_ONLY)

    def grant(self, *args: object, **kwargs: object) -> None:
        raise DriveError(_READ_ONLY)

    def trash_file(self, *args: object, **kwargs: object) -> None:
        raise DriveError(_READ_ONLY)


class ScopedDrive(ReadOnlyDrive):
    """Live-read mode's one way to write: only inside `root_id` -- dev's
    own root, never a live folder -- and the folders made in it here; every
    other write still raises. /clone-links files its reviewed videos on dev
    through it while /drive keeps reading the live folders."""

    def __init__(self, inner: DriveApi, root_id: str) -> None:
        super().__init__(inner)
        self._writable = {root_id}

    def _inside(self, parent_id: str) -> None:
        if parent_id not in self._writable:
            raise DriveError(_READ_ONLY)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:  # type: ignore[override]
        self._inside(parent_id)
        made = self._inner.ensure_folder(parent_id, name)
        self._writable.add(made.id)
        return made

    def upload(  # type: ignore[override]
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        self._inside(parent_id)
        return self._inner.upload(parent_id, path, name, mime_type)


def _guard(drive: DriveApi) -> DriveApi:
    """The Drive as callers get it: read-only in live-read mode, else as is."""
    return ReadOnlyDrive(drive) if read_live_mode() else drive


# Full Drive: the folder is the owner's, shared to the account. The
# narrower drive.file scope sees only files the app itself created.
SCOPES = ("https://www.googleapis.com/auth/drive",)
_FILE_FIELDS = "id, name, mimeType, webViewLink, hasThumbnail, createdTime, size"
_THUMB_SIZE = re.compile(r"=s\d+$")
_THUMB_TIMEOUT_S = 20
# Resumable upload chunks: a dropped connection resends one chunk, not the
# whole video.
_UPLOAD_CHUNK = 8 * 1024 * 1024
# Parents per `children_of` query: keeps the `q` string well inside what
# files.list accepts.
_PARENTS_PER_QUERY = 40


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


def _transport(timeout_s: float | None) -> httplib2.Http:
    """httplib2 counts 308 as a redirect (0.16+), but a resumable upload's
    308 is "Resume Incomplete": following it failed every upload bigger
    than one chunk with "Redirected but the response is missing a Location:
    header" (dev, 2026-10-22). googleapiclient handles the 308 itself.
    https://github.com/googleapis/google-api-python-client/issues/803"""
    http = httplib2.Http(timeout=timeout_s)
    http.redirect_codes = http.redirect_codes - {308}
    return http


class Drive:
    """The real Drive (`DriveApi`)."""

    def __init__(
        self,
        credentials: Credentials,
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
            http = AuthorizedHttp(credentials, http=_transport(timeout_s))
        service = build("drive", "v3", cache_discovery=False, http=http)
        self._files = service.files()
        self._permissions = service.permissions()
        self._about = service.about()
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

    def storage(self) -> DriveStorage:
        """The person's space: used, and the limit (none: unlimited)."""
        found = self._run(self._about.get(fields="storageQuota"), "Storage")
        quota = found.get("storageQuota", {})
        limit = quota.get("limit")
        return DriveStorage(
            usage=int(quota.get("usage", 0)), limit=int(limit) if limit else None
        )

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

    def children_of(self, parent_ids: list[str]) -> dict[str, list[DriveFile]]:
        """What is in each of several folders, newest first, trash left out:
        one listing per `_PARENTS_PER_QUERY` folders instead of one each.
        Every id must be a live folder (Drive 404s the whole query
        otherwise); /drive passes only ids its root listing just returned."""
        found: dict[str, list[DriveFile]] = {p: [] for p in parent_ids}
        for start in range(0, len(parent_ids), _PARENTS_PER_QUERY):
            chunk = parent_ids[start : start + _PARENTS_PER_QUERY]
            either = " or ".join(f"'{_quoted(p)}' in parents" for p in chunk)
            rows = self._list(
                f"({either}) and trashed = false",
                f"{_FILE_FIELDS}, parents",
                f"Listing {len(chunk)} folders",
            )
            for row in rows:
                item = DriveFile.model_validate(row)
                for parent in item.parents:
                    if parent in found:
                        found[parent].append(item)
        return found

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

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        """`path` into the folder as `name`, resumable. Only an account with
        storage (a person's, `connect_as`) can: the service account's 403s."""
        media = MediaFileUpload(
            str(path), mimetype=mime_type, resumable=True, chunksize=_UPLOAD_CHUNK
        )
        made = self._run(
            self._files.create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields=f"{_FILE_FIELDS}, parents",
                supportsAllDrives=True,
            ),
            f"Uploading {name!r}",
        )
        logger.info("drive: uploaded %r (%s)", name, made["id"])
        return DriveFile.model_validate(made)

    def grant(self, item_id: str, email: str) -> None:
        """Editor access to the item for `email`, without Google's email
        about it. Granting what someone already has changes nothing."""
        self._run(
            self._permissions.create(
                fileId=item_id,
                body={"type": "user", "role": "writer", "emailAddress": email},
                sendNotificationEmail=False,
                supportsAllDrives=True,
                fields="id",
            ),
            f"Sharing {item_id} with {email}",
        )

    def trash_file(self, item_id: str) -> None:
        """Into Drive's trash (restorable there for 30 days), never deleted
        for good. Raises `FolderGoneError` when it is already gone: asked
        with a get first, since an update of a missing id answers 500 about
        one time in five (a get 404s; probed 2026-10-24), and a 500 is
        retried, then fails as a `DriveError` (~40 s)."""
        self._run(
            self._files.get(fileId=item_id, supportsAllDrives=True, fields="id"),
            f"File {item_id}",
        )
        self._run(
            self._files.update(
                fileId=item_id,
                body={"trashed": True},
                supportsAllDrives=True,
                fields="id",
            ),
            f"File {item_id}",
        )
        logger.info("drive: trashed %s", item_id)


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

        return _guard(fake_drive())
    s = settings.gdrive
    key = _key()
    timeout_s, retries = (
        (s.interactive_timeout_s, s.interactive_retries)
        if interactive
        else (None, None)
    )
    cache_key: tuple = (key, timeout_s, retries, s.retries)
    drives: dict = _THREAD.__dict__.setdefault("drives", {})
    if cache_key not in drives:
        drives[cache_key] = Drive(
            _credentials(key), timeout_s=timeout_s, retries=retries
        )
    return _guard(drives[cache_key])


def connect_as(
    refresh_token: str, *, interactive: bool = False, guard: bool = True
) -> DriveApi:
    """This thread's `Drive` acting as a person (their refresh token, from
    signing in with Google; `signin.google`): everything /drive does goes
    through the folder owner's own account. `interactive`: a page waits
    (short timeout, few retries; see `connect`). The fake one, as that
    person, under OFM_FAKE_EXTERNALS."""
    if settings.infra.fake_externals:
        from ofmhelpers.gdrive.fake import fake_person

        person = fake_person(refresh_token)
        return _guard(person) if guard else person
    from ofmhelpers.signin.google import drive_credentials

    s = settings.gdrive
    timeout_s, retries = (
        (s.interactive_timeout_s, s.interactive_retries)
        if interactive
        else (None, None)
    )
    drives: dict = _THREAD.__dict__.setdefault("people", {})
    key = (refresh_token, timeout_s, retries, s.retries)
    if key not in drives:
        drives[key] = Drive(
            drive_credentials(refresh_token), timeout_s=timeout_s, retries=retries
        )
    # guard=False: only for a caller that wraps it in `ScopedDrive` at once.
    return _guard(drives[key]) if guard else drives[key]
