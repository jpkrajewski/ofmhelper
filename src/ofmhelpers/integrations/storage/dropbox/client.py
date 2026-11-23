import mimetypes
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote

import requests
from dropbox import Dropbox, create_session
from dropbox.exceptions import ApiError, AuthError, DropboxException
from dropbox.files import (
    CommitInfo,
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
    PathOrLink,
    ThumbnailSize,
    UploadSessionCursor,
    WriteMode,
)

from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.schema import DriveFile
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.storage.base import (
    DriveAuthError,
    DriveError,
    DriveFullError,
    DriveNotSetUpError,
    FolderGoneError,
    StorageClient,
)
from ofmhelpers.integrations.storage.dropbox.constants import dropbox_constants
from ofmhelpers.integrations.storage.gdrive.sheets import read_xlsx
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

logger = get_logger(__name__)


def _failure(exc: Exception, what: str) -> DriveError:
    logger.warning("dropbox %s failed", what, exc_info=exc)
    if isinstance(exc, AuthError):
        return DriveAuthError(dropbox_constants().reconnect)
    if isinstance(exc, ApiError):
        reason = str(exc.error)
        if dropbox_constants().not_found_marker in reason:
            return FolderGoneError(f"Dropbox {what}: gone")
        if dropbox_constants().no_space_marker in reason:
            return DriveFullError(dropbox_constants().drive_full)
    return DriveError(f"Dropbox {what}: {exc}")


@contextmanager
def _guard(what: str) -> Iterator[None]:
    try:
        yield
    except (DropboxException, requests.RequestException) as exc:
        raise _failure(exc, what) from exc


@lru_cache(maxsize=1)
def _thumbnail_sizes() -> MappingProxyType[int, ThumbnailSize]:
    return MappingProxyType(
        {
            32: ThumbnailSize.w32h32,
            64: ThumbnailSize.w64h64,
            128: ThumbnailSize.w128h128,
            256: ThumbnailSize.w256h256,
            480: ThumbnailSize.w480h320,
            640: ThumbnailSize.w640h480,
            960: ThumbnailSize.w960h640,
            1024: ThumbnailSize.w1024h768,
            2048: ThumbnailSize.w2048h1536,
        }
    )


def _is_image(name: str) -> bool:
    return name.lower().endswith(dropbox_constants().image_suffixes)


def _web_url(path_display: str) -> str:
    return f"{dropbox_constants().my_drive_url}{quote(path_display)}"


def _parent_path(path_lower: str) -> str:
    return path_lower.rsplit("/", 1)[0]


def open_api(refresh_token: str, *, interactive: bool) -> Dropbox:
    s = settings.dropbox
    if not s.app_key or not (s.app_secret and s.app_secret.get_secret_value()):
        raise DriveNotSetUpError(dropbox_constants().not_set_up)
    return Dropbox(
        oauth2_refresh_token=refresh_token,
        app_key=s.app_key,
        app_secret=s.app_secret.get_secret_value(),
        session=outbound.mount(create_session()),
        timeout=s.interactive_timeout_s if interactive else s.timeout_s,
        max_retries_on_error=1 if interactive else s.tries,
        max_retries_on_rate_limit=1 if interactive else s.tries,
    )


class DropboxDrive:
    def __init__(self, refresh_token: str, *, interactive: bool = False) -> None:
        self._api = open_api(refresh_token, interactive=interactive)

    def _is_root(self, item_id: str) -> bool:
        return item_id == dropbox_constants().root_alias

    def _refuse_root(self, item_id: str, action: str) -> None:
        if self._is_root(item_id) or not item_id.strip("/"):
            logger.warning("dropbox: refused to %s %r", action, item_id)
            msg = f"Dropbox {item_id!r} cannot be used to {action}"
            raise FolderGoneError(msg)

    def _ref(self, item_id: str) -> str:
        if self._is_root(item_id):
            return ""
        if not item_id.startswith(dropbox_constants().ref_prefixes):
            logger.info("dropbox: %r is not a Dropbox id", item_id)
            msg = f"{item_id!r} is not a Dropbox id"
            raise FolderGoneError(msg)
        return item_id

    def _meta(self, item_id: str) -> FileMetadata | FolderMetadata:
        if self._is_root(item_id):
            msg = f"Dropbox root {item_id!r} has no metadata of its own"
            raise FolderGoneError(msg)
        ref = self._ref(item_id)
        with _guard(f"metadata {item_id}"):
            meta = self._api.files_get_metadata(ref)
        if isinstance(meta, DeletedMetadata):
            msg = f"Dropbox {item_id} is gone"
            raise FolderGoneError(msg)
        return meta

    def _path(self, item_id: str) -> str:
        if self._is_root(item_id):
            return ""
        return self._meta(item_id).path_lower or ""

    def _parent_id(self, path_lower: str) -> str:
        parent = _parent_path(path_lower)
        if not parent:
            return dropbox_constants().root_alias
        return self._meta(parent).id

    def _entry(self, meta: FileMetadata | FolderMetadata, parent_id: str) -> DriveFile:
        path_display = meta.path_display or meta.name
        if isinstance(meta, FolderMetadata):
            return DriveFile.model_validate(
                {
                    "id": meta.id,
                    "name": meta.name,
                    "is_folder": True,
                    "web_view_link": _web_url(path_display),
                    "owned_by_me": True,
                    "parents": [parent_id],
                }
            )
        return DriveFile.model_validate(
            {
                "id": meta.id,
                "name": meta.name,
                "mime_type": mimetypes.guess_type(meta.name)[0] or "",
                "web_view_link": _web_url(path_display),
                "has_thumbnail": _is_image(meta.name),
                "created_time": meta.server_modified.replace(tzinfo=UTC),
                "size": meta.size,
                "owned_by_me": True,
                "parents": [parent_id],
            }
        )

    def _folder_of(self, meta: FolderMetadata, parent_id: str) -> DriveFolder:
        return DriveFolder(id=meta.id, name=meta.name, parents=[parent_id])

    def folder(self, folder_id: str) -> DriveFolder:
        if self._is_root(folder_id):
            return DriveFolder(id=folder_id, name=dropbox_constants().label)
        meta = self._meta(folder_id)
        if not isinstance(meta, FolderMetadata):
            msg = f"Dropbox {folder_id} is not a folder"
            raise FolderGoneError(msg)
        return self._folder_of(meta, self._parent_id(meta.path_lower or ""))

    def children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[DriveFile]:
        items: list[DriveFile] = []
        with _guard(f"list {parent_id}"):
            page = self._api.files_list_folder(self._ref(parent_id))
            while True:
                for meta in page.entries:
                    if isinstance(meta, DeletedMetadata):
                        continue
                    if folders_only and not isinstance(meta, FolderMetadata):
                        continue
                    items.append(self._entry(meta, parent_id))
                if not page.has_more:
                    break
                page = self._api.files_list_folder_continue(page.cursor)
        return items

    def children_of(self, parent_ids: list[str]) -> DriveListing:
        return DriveListing(
            folders=[
                FolderItems(folder_id=parent_id, items=self.children(parent_id))
                for parent_id in parent_ids
            ]
        )

    def find_folder(self, parent_id: str, name: str) -> DriveFolder | None:
        wanted = name.casefold()
        for item in self.children(parent_id, folders_only=True):
            if item.name.casefold() == wanted:
                return DriveFolder(id=item.id, name=item.name, parents=[parent_id])
        return None

    def create_folder(self, parent_id: str, name: str) -> DriveFolder:
        path = f"{self._path(parent_id)}/{name}"
        what = f"create folder {name!r}"
        try:
            made = self._api.files_create_folder_v2(path)
        except ApiError as exc:
            adopted = (
                self.find_folder(parent_id, name)
                if dropbox_constants().conflict_marker in str(exc.error)
                else None
            )
            if adopted is None:
                raise _failure(exc, what) from exc
            logger.info("dropbox: adopted folder %r (%s)", name, adopted.id)
            return adopted
        except (DropboxException, requests.RequestException) as exc:
            raise _failure(exc, what) from exc
        logger.info("dropbox: created folder %r (%s)", name, made.metadata.id)
        return self._folder_of(made.metadata, parent_id)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFolder:
        return self.find_folder(parent_id, name) or self.create_folder(parent_id, name)

    def rename(self, folder_id: str, name: str) -> DriveFolder:
        current = self._meta(folder_id)
        if not isinstance(current, FolderMetadata):
            msg = f"Dropbox {folder_id} is not a folder"
            raise FolderGoneError(msg)
        path = current.path_lower or ""
        with _guard(f"rename {folder_id}"):
            moved = self._api.files_move_v2(path, f"{_parent_path(path)}/{name}")
        if not isinstance(moved.metadata, FolderMetadata):
            msg = f"Dropbox {folder_id} is not a folder"
            raise FolderGoneError(msg)
        logger.info("dropbox: renamed folder %s to %r", folder_id, name)
        return self._folder_of(moved.metadata, self._parent_id(path))

    def file(self, file_id: str) -> DriveFile:
        meta = self._meta(file_id)
        return self._entry(meta, self._parent_id(meta.path_lower or ""))

    def find_file(self, parent_id: str, name: str) -> DriveFile | None:
        wanted = name.casefold()
        for item in self.children(parent_id):
            if not item.is_folder and item.name.casefold() == wanted:
                return item
        return None

    def sheet(self, file_id: str) -> DriveSheet:
        downloaded = self.download(file_id)
        if not downloaded.name.lower().endswith(dropbox_constants().xlsx_suffix):
            raise DriveError(dropbox_constants().no_sheets)
        try:
            return read_xlsx(downloaded.content)
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
            msg = f"Dropbox sheet {file_id} could not be read"
            logger.warning(msg, exc_info=exc)
            raise DriveError(msg) from exc

    def find_sheet(self, parent_id: str, name: str) -> DriveFile | None:
        return self.find_file(parent_id, f"{name}{dropbox_constants().xlsx_suffix}")

    def create_sheet(self, parent_id: str, name: str, content: bytes) -> DriveFile:
        file_name = f"{name}{dropbox_constants().xlsx_suffix}"
        target = f"{self._path(parent_id)}/{file_name}"
        with _guard(f"making the sheet {file_name!r}"):
            meta = self._api.files_upload(
                content, target, mode=WriteMode.add, autorename=False
            )
        logger.info("dropbox: made the sheet %r (%s)", file_name, meta.id)
        return self._entry(meta, parent_id)

    def thumbnail(self, item: DriveFile, px: int) -> DriveThumb | None:
        if not _is_image(item.name):
            return None
        self._refuse_root(item.id, "thumbnail")
        sizes = _thumbnail_sizes()
        width = next((w for w in sorted(sizes) if w >= px), max(sizes))
        location = PathOrLink.path(self._ref(item.id))
        with _guard(f"thumbnail {item.id}"):
            _, response = self._api.files_get_thumbnail_v2(location, size=sizes[width])
        with response:
            return DriveThumb(content=response.content, media_type="image/jpeg")

    def upload(
        self,
        parent_id: str,
        path: Path,
        name: str,
        mime_type: str,  # noqa: ARG002
    ) -> DriveFile:
        target = f"{self._path(parent_id)}/{name}"
        c = dropbox_constants()
        size = path.stat().st_size
        with _guard(f"upload {name!r}"), path.open("rb") as source:
            if size <= c.upload_chunk_bytes:
                meta = self._api.files_upload(
                    source.read(), target, mode=WriteMode.add, autorename=False
                )
            else:
                started = self._api.files_upload_session_start(
                    source.read(c.upload_chunk_bytes)
                )
                cursor = UploadSessionCursor(started.session_id, source.tell())
                commit = CommitInfo(target, mode=WriteMode.add, autorename=False)
                while size - cursor.offset > c.upload_chunk_bytes:
                    self._api.files_upload_session_append_v2(
                        source.read(c.upload_chunk_bytes), cursor
                    )
                    cursor.offset = source.tell()
                meta = self._api.files_upload_session_finish(
                    source.read(), cursor, commit
                )
        logger.info("dropbox: uploaded %r (%s)", name, meta.id)
        return self._entry(meta, parent_id)

    def trash_file(self, item_id: str) -> None:
        self._refuse_root(item_id, "trash")
        with _guard(f"trash {item_id}"):
            self._api.files_delete_v2(self._ref(item_id))
        logger.info("dropbox: trashed %s", item_id)

    def web_url(self, item_id: str) -> str:
        if self._is_root(item_id):
            return dropbox_constants().my_drive_url
        return _web_url(self._meta(item_id).path_display or "")

    def storage(self) -> DriveStorage:
        try:
            usage = self._api.users_get_space_usage()
        except (DropboxException, requests.RequestException) as exc:
            raise _failure(exc, "space usage") from exc
        allocation = usage.allocation
        if allocation.is_individual():
            limit = allocation.get_individual().allocated
        elif allocation.is_team():
            limit = allocation.get_team().allocated
        else:
            limit = None
        return DriveStorage(usage=usage.used, limit=limit)

    def download(self, file_id: str) -> DriveDownload:
        self._refuse_root(file_id, "download")
        ref = self._ref(file_id)
        try:
            meta, response = self._api.files_download(ref)
        except ApiError as exc:
            if exc.error.is_path() and exc.error.get_path().is_not_found():
                msg = f"Dropbox file {file_id} is gone"
                raise FolderGoneError(msg) from exc
            raise _failure(exc, f"download {file_id}") from exc
        except (DropboxException, requests.RequestException) as exc:
            raise _failure(exc, f"download {file_id}") from exc
        with response:
            return DriveDownload(content=response.content, name=meta.name)


def connect_as(refresh_token: str, *, interactive: bool) -> StorageClient:
    return DropboxDrive(refresh_token, interactive=interactive)
