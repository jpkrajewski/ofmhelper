import logging
from datetime import UTC, datetime

import pytest
import requests
from dropbox.files import FileMetadata, FolderMetadata

from ofmhelpers.db.drive_folders.schema import DriveFile
from ofmhelpers.integrations.storage.base import (
    DriveAuthError,
    DriveError,
    FolderGoneError,
)
from ofmhelpers.integrations.storage.dropbox import client as dropbox_client
from ofmhelpers.integrations.storage.dropbox import oauth
from ofmhelpers.integrations.storage.gdrive import client as gdrive_client


class NoSdkCall:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def files_get_metadata(self, path: str) -> None:
        self.calls.append(path)

    def files_list_folder(self, path: str) -> None:
        self.calls.append(path)


@pytest.fixture
def api(monkeypatch) -> NoSdkCall:
    stub = NoSdkCall()
    monkeypatch.setattr(dropbox_client, "open_api", lambda *_, **__: stub)
    return stub


@pytest.mark.parametrize("stale_id", ["1AbC_google_folder_id", "mydrive-x", "abc:def"])
def test_a_google_shaped_id_is_a_gone_folder_and_the_sdk_is_not_asked(
    api, stale_id, caplog
):
    drive = dropbox_client.DropboxDrive("refresh")

    with caplog.at_level(logging.INFO), pytest.raises(FolderGoneError):
        drive.folder(stale_id)
    with pytest.raises(FolderGoneError):
        drive.children(stale_id)
    with pytest.raises(FolderGoneError):
        drive.find_folder(stale_id, "x")

    assert api.calls == []
    assert any("not a Dropbox id" in r.message for r in caplog.records)


class RecordingApi:
    def __init__(self, meta: FileMetadata | FolderMetadata) -> None:
        self.meta = meta
        self.calls: list[str] = []

    def files_get_metadata(self, path: str) -> FileMetadata | FolderMetadata:
        self.calls.append(f"metadata {path}")
        return self.meta

    def files_move_v2(self, source: str, target: str) -> None:
        self.calls.append(f"move {source} {target}")

    def files_delete_v2(self, path: str) -> None:
        self.calls.append(f"delete {path}")


def _file_meta() -> FileMetadata:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    return FileMetadata(
        name="a.mp4",
        id="id:file",
        client_modified=moment,
        server_modified=moment,
        rev="0123456789",
        size=1,
        path_lower="/a/a.mp4",
        path_display="/A/a.mp4",
    )


def test_renaming_a_file_is_refused_before_any_move(monkeypatch):
    api = RecordingApi(_file_meta())
    monkeypatch.setattr(dropbox_client, "open_api", lambda *_, **__: api)
    drive = dropbox_client.DropboxDrive("refresh")

    with pytest.raises(FolderGoneError):
        drive.rename("id:file", "new")

    assert [call for call in api.calls if call.startswith("move")] == []


@pytest.mark.parametrize("item_id", ["root", "", "/", "//"])
def test_trashing_the_root_or_nothing_asks_no_sdk_call(monkeypatch, item_id):
    api = RecordingApi(_file_meta())
    monkeypatch.setattr(dropbox_client, "open_api", lambda *_, **__: api)
    drive = dropbox_client.DropboxDrive("refresh")

    with pytest.raises(FolderGoneError):
        drive.trash_file(item_id)

    assert api.calls == []


@pytest.mark.parametrize("item_id", ["root", "", "/", "//"])
def test_the_root_or_nothing_is_not_downloaded_or_thumbnailed(api, item_id):
    drive = dropbox_client.DropboxDrive("refresh")

    with pytest.raises(FolderGoneError):
        drive.download(item_id)
    with pytest.raises(FolderGoneError):
        drive.thumbnail(DriveFile(id=item_id, name="a.jpg"), 220)

    assert api.calls == []


def test_a_google_shaped_id_is_not_downloaded_or_thumbnailed(api):
    drive = dropbox_client.DropboxDrive("refresh")
    google_file = DriveFile(id="1AbC_google_file_id", name="a.jpg")

    with pytest.raises(FolderGoneError):
        drive.download(google_file.id)
    with pytest.raises(FolderGoneError):
        drive.thumbnail(google_file, 220)

    assert api.calls == []


def test_trashing_a_file_deletes_by_its_dropbox_id(monkeypatch):
    api = RecordingApi(_file_meta())
    monkeypatch.setattr(dropbox_client, "open_api", lambda *_, **__: api)

    dropbox_client.DropboxDrive("refresh").trash_file("id:file")

    assert api.calls == ["delete id:file"]


class RecordingRequest:
    def __init__(self, calls: list[int | None]) -> None:
        self.calls = calls

    def execute(self, num_retries: int | None = None) -> dict[str, str]:
        self.calls.append(num_retries)
        return {
            "id": "sheet-id",
            "name": "n.xlsx",
            "mimeType": "x",
            "createdTime": "2026-01-01T00:00:00Z",
        }


class RecordingFiles:
    def __init__(self) -> None:
        self.retries: list[int | None] = []

    def create(self, **_: object) -> RecordingRequest:
        return RecordingRequest(self.retries)


def test_google_create_sheet_is_never_retried():
    drive = object.__new__(gdrive_client.Drive)
    files = RecordingFiles()
    drive._files = files
    drive._retries = 5

    drive.create_sheet("parent", "n", b"content")

    assert files.retries == [0]


def _reply(status: int, body: str = "") -> requests.Response:
    made = requests.Response()
    made.status_code = status
    made._content = body.encode()
    return made


def _answer(monkeypatch, status: int, body: str = "") -> None:
    monkeypatch.setattr(oauth, "_token_call", lambda _: _reply(status, body))


def _exchange() -> oauth.DropboxGrant:
    return oauth.exchange("code", "https://app/cb", "verifier")


@pytest.mark.parametrize("status", [400, 401])
def test_a_refused_code_asks_to_reconnect(monkeypatch, status):
    _answer(monkeypatch, status)
    with pytest.raises(DriveAuthError):
        _exchange()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_a_rate_limit_or_server_error_is_no_answer_not_a_reconnect(monkeypatch, status):
    _answer(monkeypatch, status)
    with pytest.raises(DriveError) as caught:
        _exchange()
    assert not isinstance(caught.value, DriveAuthError)
    assert "Reconnect" not in str(caught.value)


def test_a_token_answer_missing_a_field_names_the_field_and_never_a_token(
    monkeypatch, caplog
):
    _answer(
        monkeypatch, 200, '{"access_token": "sl.SECRET-ACCESS", "account_id": "dbid:1"}'
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(DriveError) as caught:
        _exchange()

    assert "refresh_token" in str(caught.value)
    assert "SECRET-ACCESS" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    logged = "\n".join(r.getMessage() + str(r.exc_info) for r in caplog.records)
    assert "SECRET-ACCESS" not in logged
    assert all(r.exc_info is None for r in caplog.records)


def test_a_token_answer_that_is_not_json_carries_no_text(monkeypatch):
    _answer(monkeypatch, 200, "<html>sl.SECRET-ACCESS</html>")
    with pytest.raises(DriveError) as caught:
        _exchange()
    assert "SECRET-ACCESS" not in str(caught.value)
