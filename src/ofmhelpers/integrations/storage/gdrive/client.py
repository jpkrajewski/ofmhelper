import io
import json
import re
import threading
import zipfile
from functools import lru_cache
from http import HTTPStatus
from typing import Any
lazy from pathlib import Path

import logfire
from pydantic import BaseModel
lazy import httplib2
lazy from google.auth.credentials import Credentials
lazy from google.auth.exceptions import GoogleAuthError, RefreshError
lazy from google.auth.transport.requests import AuthorizedSession
lazy from google.oauth2 import service_account
lazy from google.oauth2.credentials import Credentials as OAuthCredentials
lazy from google_auth_httplib2 import AuthorizedHttp
lazy from googleapiclient.discovery import build
lazy from googleapiclient.errors import Error as GoogleApiError
lazy from googleapiclient.errors import HttpError
lazy from googleapiclient.http import HttpRequest, MediaFileUpload, MediaIoBaseUpload

from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.schema import (
    FOLDER_MIME,
    DriveFile,
)
from ofmhelpers.integrations.storage.base import (
    DriveAuthError,
    DriveError,
    DriveFullError,
    DriveNotSetUpError,
    FolderGoneError,
    FolderTrashedError,
    StorageClient,
)
from ofmhelpers.integrations.storage.gdrive.constants import gdrive_constants
from ofmhelpers.integrations.storage.gdrive.models import (
    DriveAbout,
    DriveAck,
    DriveRow,
    DriveRowList,
)
from ofmhelpers.integrations.storage.gdrive.sheets import XLSX_MIME, read_xlsx
from ofmhelpers.integrations.storage.models import (
    DriveDownload,
    DriveFolder,
    DriveListing,
    DriveSheet,
    DriveStorage,
    DriveThumb,
    FolderItems,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.storage.gdrive.fake import (
    FAKE_BOT_EMAIL,
    fake_drive,
    fake_person,
)

logger = get_logger(__name__)


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive",)
_FILE_FIELDS = "id, name, mimeType, webViewLink, hasThumbnail, createdTime, size"
_THUMB_SIZE = re.compile(r"=s\d+$")
_THUMB_TIMEOUT_S = 20
_DOWNLOAD_TIMEOUT_S = 300
_UPLOAD_CHUNK = 8 * 1024 * 1024
_PARENTS_PER_QUERY = 40


@lru_cache(maxsize=1)
def _credentials(key_json: str) -> service_account.Credentials:
    try:
        return service_account.Credentials.from_service_account_info(
            json.loads(key_json), scopes=SCOPES
        )
    except (ValueError, KeyError, GoogleAuthError) as exc:
        msg = "OFM_GDRIVE_SERVICE_ACCOUNT is not a service account key (JSON)"
        raise DriveError(msg) from exc


def drive_credentials(refresh_token: str) -> Credentials:
    s = settings.signin
    if not s.google_configured or s.google_client_secret is None:
        msg = "Google Drive sign in is not set up"
        raise DriveNotSetUpError(msg)
    return OAuthCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URL,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret.get_secret_value(),
    )


def _key() -> str:
    key = settings.gdrive.service_account
    if key is None:
        msg = "Google Drive is not set up: OFM_GDRIVE_SERVICE_ACCOUNT is empty"
        raise DriveError(msg)
    return key.get_secret_value()


def service_account_email() -> str | None:
    if settings.infra.fake_externals:
        return FAKE_BOT_EMAIL
    try:
        return _credentials(_key()).service_account_email
    except DriveError:
        logger.warning("drive: service account not usable", exc_info=True)
        return None


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


_SCOPE_REASONS = frozenset(
    {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "authError"}
)
_FULL_REASON = "storageQuotaExceeded"


def _reasons(exc: HttpError) -> set[str]:
    details = exc.error_details if isinstance(exc.error_details, list) else []
    return {str(d["reason"]) for d in details if isinstance(d, dict) and "reason" in d}


def _auth_refused(exc: Exception) -> bool:
    if isinstance(exc, RefreshError):
        return not exc.retryable
    if not isinstance(exc, HttpError):
        return False
    if exc.resp.status == HTTPStatus.UNAUTHORIZED:
        return True
    return exc.resp.status == HTTPStatus.FORBIDDEN and bool(
        _reasons(exc) & _SCOPE_REASONS
    )


def _refused(exc: HttpError, what: str) -> DriveError:
    message = f"{what}: Drive refused ({exc.reason})"
    if _FULL_REASON in _reasons(exc) or "storage quota" in exc.reason.lower():
        return DriveFullError(message)
    return DriveError(message)


def _failure(exc: Exception, what: str) -> DriveError:
    if _auth_refused(exc):
        logger.warning("drive: %s: Google refused the sign-in: %s", what, exc)
        return DriveAuthError()
    if isinstance(exc, HttpError) and exc.resp.status == HTTPStatus.NOT_FOUND:
        return FolderGoneError(f"{what}: not found, or not shared with the bot")
    if isinstance(exc, HttpError) and exc.resp.status == HTTPStatus.FORBIDDEN:
        return _refused(exc, what)
    return DriveError(f"{what}: {exc}")


def _transport(timeout_s: float | None) -> httplib2.Http:
    http = httplib2.Http(timeout=timeout_s)
    http.redirect_codes = http.redirect_codes - {308}
    return http


class Drive:
    def __init__(
        self,
        credentials: Credentials,
        http: Any = None,
        *,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> None:
        self._credentials = credentials
        if http is None:
            http = AuthorizedHttp(credentials, http=_transport(timeout_s))
        service = build("drive", "v3", cache_discovery=False, http=http)
        self._files = service.files()
        self._about = service.about()
        self._retries = settings.gdrive.retries if retries is None else retries

    def _run[M: BaseModel](
        self,
        request: HttpRequest,
        what: str,
        shape: type[M],
        *,
        retries: int | None = None,
    ) -> M:
        try:
            raw = request.execute(
                num_retries=self._retries if retries is None else retries
            )
        except (
            GoogleApiError,
            GoogleAuthError,
            OSError,
            httplib2.HttpLib2Error,
        ) as exc:
            raise _failure(exc, what) from exc
        return shape.model_validate(raw)

    def storage(self) -> DriveStorage:
        found = self._run(self._about.get(fields="storageQuota"), "Storage", DriveAbout)
        quota = found.storage_quota
        return DriveStorage(usage=quota.usage, limit=quota.limit)

    def folder(self, folder_id: str) -> DriveFolder:
        found = self._run(
            self._files.get(
                fileId=folder_id,
                fields="id, name, mimeType, trashed, parents",
                supportsAllDrives=True,
            ),
            f"Folder {folder_id}",
            DriveRow,
        )
        if found.trashed or found.mime_type != FOLDER_MIME:
            msg = f"Folder {folder_id} is trashed or not a folder"
            raise FolderTrashedError(msg)
        return DriveFolder(id=found.id, name=found.name, parents=found.parents)

    def _list(self, query: str, fields: str, what: str) -> list[DriveRow]:
        items: list[DriveRow] = []
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
                DriveRowList,
            )
            items.extend(found.files)
            page = found.next_page_token
            if not page:
                return items

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        query = f"'{_quoted(parent_id)}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"
        rows = self._list(query, _FILE_FIELDS, f"Listing folder {parent_id}")
        return [row.as_file() for row in rows]

    def children_of(self, parent_ids: list[str]) -> DriveListing:
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
                item = row.as_file()
                for parent in item.parents:
                    if parent in found:
                        found[parent].append(item)
        return DriveListing(
            folders=[FolderItems(folder_id=p, items=found[p]) for p in found]
        )

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        query = (
            f"'{_quoted(parent_id)}' in parents and name = '{_quoted(name)}'"
            f" and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        rows = self._list(query, "id, name, createdTime", f"Finding {name!r}")
        if not rows:
            return None
        oldest = min(rows, key=lambda row: row.created_time)
        return DriveFolder(id=oldest.id, name=oldest.name)

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        made = self._run(
            self._files.create(body=body, fields="id, name", supportsAllDrives=True),
            f"Creating {name!r}",
            DriveRow,
        )
        logger.info("drive: created folder %r (%s)", made.name, made.id)
        return DriveFolder(id=made.id, name=made.name)

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
            DriveRow,
        )
        logger.info("drive: renamed folder %s to %r", folder_id, name)
        return DriveFolder(id=done.id, name=done.name)

    def file(self, file_id: str) -> DriveFile:
        found = self._run(
            self._files.get(
                fileId=file_id,
                fields=f"{_FILE_FIELDS}, parents, thumbnailLink, ownedByMe, trashed",
                supportsAllDrives=True,
            ),
            f"File {file_id}",
            DriveRow,
        )
        if found.trashed:
            msg = f"File {file_id} is trashed"
            raise FolderGoneError(msg)
        return found.as_file()

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        query = (
            f"'{_quoted(parent_id)}' in parents and name = '{_quoted(name)}'"
            f" and mimeType != '{FOLDER_MIME}' and trashed = false"
        )
        rows = self._list(query, _FILE_FIELDS, f"Finding {name!r}")
        if not rows:
            return None
        oldest = min(rows, key=lambda row: row.created_time)
        return oldest.as_file()

    def sheet(self, file_id: str) -> DriveSheet:
        what = f"Sheet {file_id}"
        try:
            content = self._files.export(fileId=file_id, mimeType=XLSX_MIME).execute(
                num_retries=self._retries
            )
        except (
            GoogleApiError,
            GoogleAuthError,
            OSError,
            httplib2.HttpLib2Error,
        ) as exc:
            raise _failure(exc, what) from exc
        limit = settings.gdrive.sheet_max_bytes
        if len(content) > limit:
            msg = f"{what}: too big to read ({len(content)} bytes, limit {limit})"
            raise DriveError(msg)
        try:
            return read_xlsx(content)
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
            msg = f"{what}: Drive's export could not be read"
            raise DriveError(msg) from exc

    def find_sheet(self, parent_id: str, name: str) -> DriveFile | None:
        return self.find_file(parent_id, name)

    def create_sheet(self, parent_id: str, name: str, content: bytes) -> DriveFile:
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=XLSX_MIME)
        made = self._run(
            self._files.create(
                body={
                    "name": name,
                    "parents": [parent_id],
                    "mimeType": gdrive_constants().sheet_mime,
                },
                media_body=media,
                fields=f"{_FILE_FIELDS}, parents",
                supportsAllDrives=True,
            ),
            f"Making the sheet {name!r}",
            DriveRow,
            retries=0,
        )
        logger.info("drive: made the sheet %r (%s)", name, made.id)
        return made.as_file()

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:
        if not item.thumbnail_link:
            return None
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
        return DriveThumb(
            content=response.content,
            media_type=response.headers.get("Content-Type", "image/jpeg"),
        )

    def download(self, file_id: str) -> DriveDownload:
        item = self.file(file_id)
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        try:
            response = AuthorizedSession(self._credentials).get(
                url,
                params={"alt": "media", "supportsAllDrives": "true"},
                timeout=_DOWNLOAD_TIMEOUT_S,
            )
        except (GoogleAuthError, OSError) as exc:
            raise _failure(exc, f"Download of {file_id}") from exc
        if response.status_code != HTTPStatus.OK:
            msg = f"Download of {file_id} answered {response.status_code}"
            raise DriveError(msg)
        return DriveDownload(content=response.content, name=item.name)

    def upload(
        self, parent_id: str, path: Path, name: str, mime_type: str
    ) -> DriveFile:
        media = MediaFileUpload(
            str(path), mimetype=mime_type, resumable=True, chunksize=_UPLOAD_CHUNK
        )
        with logfire.span(
            "drive upload {name}", name=name, size_bytes=path.stat().st_size
        ):
            made = self._run(
                self._files.create(
                    body={"name": name, "parents": [parent_id]},
                    media_body=media,
                    fields=f"{_FILE_FIELDS}, parents",
                    supportsAllDrives=True,
                ),
                f"Uploading {name!r}",
                DriveRow,
            )
        logger.info("drive: uploaded %r (%s)", name, made.id)
        return made.as_file()

    def web_url(self, item_id: str) -> str:
        return gdrive_constants().folder_url.format(item_id)

    def trash_file(self, item_id: str) -> None:
        self._run(
            self._files.get(fileId=item_id, supportsAllDrives=True, fields="id"),
            f"File {item_id}",
            DriveAck,
        )
        self._run(
            self._files.update(
                fileId=item_id,
                body={"trashed": True},
                supportsAllDrives=True,
                fields="id",
            ),
            f"File {item_id}",
            DriveAck,
        )
        logger.info("drive: trashed %s", item_id)


class _PerThread(threading.local):
    def __init__(self) -> None:
        self.drives: dict[tuple, StorageClient] = {}
        self.people: dict[tuple, Drive] = {}


_THREAD = _PerThread()


def connect(*, interactive: bool = False) -> StorageClient:
    if settings.infra.fake_externals:
        return fake_drive()
    s = settings.gdrive
    key = _key()
    timeout_s, retries = (
        (s.interactive_timeout_s, s.interactive_retries)
        if interactive
        else (None, None)
    )
    cache_key: tuple = (key, timeout_s, retries, s.retries)
    drives = _THREAD.drives
    if cache_key not in drives:
        drives[cache_key] = Drive(
            _credentials(key), timeout_s=timeout_s, retries=retries
        )
    return drives[cache_key]


def connect_as(refresh_token: str, *, interactive: bool = False) -> StorageClient:
    if settings.infra.fake_externals:
        return fake_person(refresh_token)
    s = settings.gdrive
    timeout_s, retries = (
        (s.interactive_timeout_s, s.interactive_retries)
        if interactive
        else (None, None)
    )
    drives = _THREAD.people
    key = (refresh_token, timeout_s, retries, s.retries)
    if key not in drives:
        drives[key] = Drive(
            drive_credentials(refresh_token), timeout_s=timeout_s, retries=retries
        )
    return drives[key]
