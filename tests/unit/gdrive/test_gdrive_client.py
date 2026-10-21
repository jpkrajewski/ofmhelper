"""ofmhelpers.gdrive: the real googleapiclient over a scripted transport --
queries quoted, a hand-made folder reused, a missing or trashed folder a
`FolderGoneError`, failures a `DriveError`, previews at the asked size."""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from googleapiclient.http import HttpMockSequence

from ofmhelpers.gdrive import Drive, DriveError, FolderGoneError
from ofmhelpers.gdrive import client as drive_client
from ofmhelpers.gdrive.models import FOLDER_MIME, DriveFile

_OK = {"status": "200"}


def _drive(*responses: tuple[dict, object]) -> tuple[Drive, HttpMockSequence]:
    http = HttpMockSequence(
        [(r, json.dumps(b) if not isinstance(b, str) else b) for r, b in responses]
    )
    return Drive(credentials=None, http=http), http  # type: ignore[arg-type]


def _query(http: HttpMockSequence, index: int = -1) -> dict[str, list[str]]:
    return parse_qs(urlparse(http.request_sequence[index][0]).query)


def test_find_folder_quotes_the_name_and_reuses_the_oldest():
    drive, http = _drive(
        (
            _OK,
            {
                "files": [
                    {
                        "id": "new",
                        "name": "o'neil - Ann",
                        "createdTime": "2026-10-20T00:00:00Z",
                    },
                    {
                        "id": "old",
                        "name": "o'neil - Ann",
                        "createdTime": "2026-01-01T00:00:00Z",
                    },
                ]
            },
        ),
    )
    found = drive.find_folder("root", "o'neil - Ann")
    assert found is not None
    assert found.id == "old"
    q = _query(http)["q"][0]
    assert "name = 'o\\'neil - Ann'" in q
    assert "'root' in parents" in q
    assert "trashed = false" in q


def test_find_folder_none_when_absent():
    drive, _ = _drive((_OK, {"files": []}))
    assert drive.find_folder("root", "x") is None


def test_children_follow_every_page():
    page1 = {
        "files": [{"id": "a", "name": "a.mp4", "mimeType": "video/mp4"}],
        "nextPageToken": "p2",
    }
    page2 = {
        "files": [
            {"id": "b", "name": "b.jpg", "mimeType": "image/jpeg", "hasThumbnail": True}
        ]
    }
    drive, http = _drive((_OK, page1), (_OK, page2))
    files = drive.children("day")
    assert [(f.id, f.kind) for f in files] == [("a", "video"), ("b", "image")]
    assert _query(http)["pageToken"] == ["p2"]


def test_folder_missing_or_not_shared_is_gone():
    drive, _ = _drive(
        ({"status": "404"}, {"error": {"code": 404, "message": "File not found"}})
    )
    with pytest.raises(FolderGoneError):
        drive.folder("root")


def test_trashed_folder_is_gone():
    drive, _ = _drive(
        (_OK, {"id": "f", "name": "x", "mimeType": FOLDER_MIME, "trashed": True})
    )
    with pytest.raises(FolderGoneError):
        drive.folder("f")


def test_refusal_is_a_drive_error_not_http_error(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_RETRIES", "0")
    drive, _ = _drive(({"status": "403"}, {"error": {"code": 403, "message": "no"}}))
    with pytest.raises(DriveError) as caught:
        drive.create_folder("root", "x")
    assert not isinstance(caught.value, FolderGoneError)


def test_create_folder_puts_it_in_the_parent():
    drive, http = _drive((_OK, {"id": "made", "name": "ig - Ann"}))
    made = drive.create_folder("root", "ig - Ann")
    assert made.id == "made"
    body = json.loads(http.request_sequence[-1][2])
    assert body == {"name": "ig - Ann", "mimeType": FOLDER_MIME, "parents": ["root"]}


def _item(**fields) -> DriveFile:
    return DriveFile.model_validate(
        {"id": "f", "name": "a.jpg", "mimeType": "image/jpeg", **fields}
    )


def test_file_reads_parents_and_the_preview_link():
    drive, http = _drive(
        (
            _OK,
            {
                "id": "f",
                "name": "a.jpg",
                "mimeType": "image/jpeg",
                "parents": ["day"],
                "thumbnailLink": "https://lh3.example/abc=s220",
                "ownedByMe": False,
            },
        )
    )
    item = drive.file("f")
    assert item.parents == ["day"]
    assert item.thumbnail_link == "https://lh3.example/abc=s220"
    fields = _query(http)["fields"][0]
    assert "parents" in fields
    assert "thumbnailLink" in fields


def test_a_trashed_file_is_gone():
    drive, _ = _drive(
        (_OK, {"id": "f", "name": "a", "mimeType": "image/jpeg", "trashed": True})
    )
    with pytest.raises(FolderGoneError):
        drive.file("f")


def test_thumbnail_none_without_a_link():
    drive, http = _drive()
    assert drive.thumbnail(_item(), 400) is None
    assert http.request_sequence == []


def test_thumbnail_asks_for_the_size(monkeypatch):
    asked = {}

    class _Session:
        def __init__(self, credentials):
            pass

        def get(self, url, **_kwargs):
            asked["url"] = url

            class R:
                status_code = 200
                content = b"jpeg"
                headers = {"Content-Type": "image/jpeg"}

            return R()

    monkeypatch.setattr(drive_client, "AuthorizedSession", _Session)
    drive, _ = _drive()
    item = _item(thumbnailLink="https://lh3.example/abc=s220")
    assert drive.thumbnail(item, 400) == (b"jpeg", "image/jpeg")
    assert asked["url"] == "https://lh3.example/abc=s400"


def test_not_set_up_is_a_drive_error(monkeypatch):
    monkeypatch.delenv("OFM_GDRIVE_SERVICE_ACCOUNT", raising=False)
    with pytest.raises(DriveError, match="not set up"):
        drive_client.connect()
    assert drive_client.service_account_email() is None


def test_a_bad_key_is_a_drive_error(monkeypatch):
    drive_client._credentials.cache_clear()
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", '{"type": "nope"}')
    with pytest.raises(DriveError, match="not a service account key"):
        drive_client.connect()


def test_file_kinds_and_links():
    video = DriveFile.model_validate(
        {"id": "v", "name": "a.mov", "mimeType": "video/quicktime"}
    )
    doc = DriveFile.model_validate(
        {"id": "d", "name": "a.pdf", "mimeType": "application/pdf"}
    )
    folder = DriveFile.model_validate({"id": "f", "name": "x", "mimeType": FOLDER_MIME})
    assert (video.kind, doc.kind, folder.kind) == ("video", "file", "folder")
    assert video.url == "https://drive.google.com/file/d/v/view"
    assert folder.url == "https://drive.google.com/drive/folders/f"


class _NoNetwork:
    """An httplib2 transport whose every request fails like a DNS miss."""

    def request(self, *_args, **_kwargs):
        import httplib2

        msg = "Unable to find the server at www.googleapis.com"
        raise httplib2.ServerNotFoundError(msg)


def test_a_transport_failure_is_a_drive_error(monkeypatch):
    monkeypatch.setenv("OFM_GDRIVE_RETRIES", "0")
    drive = Drive(credentials=None, http=_NoNetwork())  # type: ignore[arg-type]
    with pytest.raises(DriveError, match="Unable to find the server"):
        drive.folder("root")


def test_connect_keeps_one_drive_per_thread_and_bounds_interactive_calls(
    monkeypatch,
):
    made = []

    class Recorded:
        def __init__(self, _credentials, *, timeout_s, retries):
            made.append((timeout_s, retries))

    monkeypatch.setattr(drive_client, "Drive", Recorded)
    monkeypatch.setattr(drive_client, "_key", lambda: "key-json")
    monkeypatch.setattr(drive_client, "_credentials", lambda key: object())
    monkeypatch.setenv("OFM_GDRIVE_INTERACTIVE_TIMEOUT_S", "7")
    monkeypatch.setenv("OFM_GDRIVE_INTERACTIVE_RETRIES", "1")
    drive_client._THREAD.__dict__.pop("drives", None)

    first = drive_client.connect()
    assert drive_client.connect() is first
    page = drive_client.connect(interactive=True)

    assert page is not first
    assert drive_client.connect(interactive=True) is page
    assert made == [(None, None), (7.0, 1)]
