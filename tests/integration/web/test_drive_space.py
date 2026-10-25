"""web/drive_space.py: a person's Google Drive space, asked of Google at most
once per TTL whatever it answers (a read, a refused sign-in = `reconnect`, no
answer = a shorter retry), never at all with `ask=False`; a new token (a
Reconnect) asks again. A failed upload's reason on a card is one of a few
fixed words by kind, never Drive's own text (review 2026-10-25 #13, #16,
#33, #46)."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.cache import get_redis
from ofmhelpers.gdrive import RECONNECT, DriveError, DriveFolder, connect_as
from ofmhelpers.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.signin import SignInError
from ofmhelpers.web.drive_space import (
    DRIVE_FULL,
    FOLDER_MISSING,
    NO_ANSWER,
    drive_space,
    upload_error,
)
from ofmhelpers.web.main import app
from ofmhelpers.web.stores import users

EMAIL = "ann@example.com"
_GB = 1_000_000_000


@pytest.fixture
def drive(fake_externals) -> FakeDrive:
    """The fake Drive: 10 GB, 9.7 GB used (300 MB free: under the 500 MB
    floor)."""
    fake = fake_drive()
    fake.set_storage(10 * _GB, used=int(9.7 * _GB))
    return fake


def _connected(email: str = EMAIL, token: str | None = None) -> int:
    """A person with Google Drive connected (`token`: their refresh token)."""
    user = make_user(email)
    users._repository().signed_in(user.id, "", token or f"fake-refresh:{email}")
    return user.id


def _asks(drive: FakeDrive) -> int:
    return drive.calls.count("storage")


def _space_ttls() -> list[int]:
    redis = get_redis()
    return [int(redis.ttl(key)) for key in redis.scan_iter("drive-space:*")]


def test_the_space_is_read_once_per_ttl(drive):
    user_id = _connected()

    space = drive_space(user_id)
    again = drive_space(user_id)

    assert space is not None
    assert (space.free_text, space.free_mb, space.low, space.reconnect) == (
        "300 MB",
        300,
        True,
        False,
    )
    assert again == space
    assert _asks(drive) == 1


def test_a_drive_that_does_not_answer_is_asked_once_a_minute(drive, monkeypatch):
    user_id = _connected()
    asked = []

    def down(self):
        asked.append(1)
        msg = "Storage: <HttpError 503 when requesting https://www.googleapis.com/>"
        raise DriveError(msg)

    monkeypatch.setattr(FakeDrive, "storage", down)

    assert drive_space(user_id) is None
    assert drive_space(user_id) is None
    assert len(asked) == 1
    (ttl,) = _space_ttls()
    assert 0 < ttl <= 60


def test_without_google_nothing_is_asked_or_cached(drive):
    user_id = make_user(EMAIL).id

    assert drive_space(user_id) is None
    assert _asks(drive) == 0
    assert _space_ttls() == []


def test_a_refused_sign_in_says_reconnect_until_a_new_token(drive):
    user_id = _connected()
    drive.refuse_sign_in(EMAIL)

    space = drive_space(user_id)

    assert space is not None
    assert (space.reconnect, space.low, space.free_mb, space.free_text) == (
        True,
        False,
        None,
        "",
    )
    cached = drive_space(user_id, ask=False)
    assert cached is not None
    assert cached.reconnect is True
    # Reconnect: Google hands out a new token, which is asked at once.
    _connected(token="fake-refresh:ann.again@example.com")
    fresh = drive_space(user_id)
    assert fresh is not None
    assert (fresh.reconnect, fresh.free_mb) == (False, 300)


def test_ask_false_answers_only_from_the_cache(drive):
    user_id = _connected()

    assert drive_space(user_id, ask=False) is None
    assert _asks(drive) == 0
    asked = drive_space(user_id)
    assert drive_space(user_id, ask=False) == asked
    assert _asks(drive) == 1


def test_the_page_endpoint_says_reconnect(drive):
    c = TestClient(app)
    user = sign_in(c, EMAIL)
    users._repository().signed_in(user.id, "", f"fake-refresh:{EMAIL}")
    drive.refuse_sign_in(EMAIL)

    body = c.get("/account/drive-space").json()

    assert body["reconnect"] is True
    assert body["low"] is False
    assert body["free_mb"] is None


def test_every_signed_in_page_carries_the_reconnect_pop_up_hidden(drive):
    c = TestClient(app)
    sign_in(c, EMAIL)

    page = c.get("/home").text

    assert 'class="notice notice-fail drive-reconnect-banner" role="alert" hidden' in (
        page
    )
    assert "Google Drive disconnected" in page
    assert 'drive-reconnect-link" href="/account"' in page


# --- upload_error: what a card says, by kind ------------------------------


def _video(tmp_path):
    video = tmp_path / "clone.mp4"
    video.write_bytes(b"x" * 1000)
    return video


def _upload_failure(user_id: int, folder: str, video) -> Exception:
    person = connect_as(users.refresh_token(user_id))
    with pytest.raises(DriveError) as caught:
        person.upload(folder, video, "clone.mp4", "video/mp4")
    return caught.value


def _folder(email: str = EMAIL) -> DriveFolder:
    return connect_as(f"fake-refresh:{email}").create_folder("root", "GOOD REELS")


def test_a_full_drive_says_so_and_forgets_the_cached_space(drive, tmp_path):
    user_id = _connected()
    assert drive_space(user_id) is not None
    drive.set_storage(1000, used=999)

    failure = _upload_failure(user_id, _folder().id, _video(tmp_path))

    assert upload_error(user_id, failure) == DRIVE_FULL
    assert drive_space(user_id, ask=False) is None


def test_a_refused_sign_in_says_reconnect_on_every_page_at_once(drive, tmp_path):
    user_id = _connected()
    folder = _folder()
    drive.refuse_sign_in(EMAIL)

    failure = _upload_failure(user_id, folder.id, _video(tmp_path))

    assert upload_error(user_id, failure) == RECONNECT
    cached = drive_space(user_id, ask=False)
    assert cached is not None
    assert cached.reconnect is True
    assert _asks(drive) == 0


def test_a_folder_gone_says_drive_folder_missing(drive, tmp_path):
    user_id = _connected()
    folder = _folder()
    drive.trash(folder.id)

    failure = _upload_failure(user_id, folder.id, _video(tmp_path))

    assert "not found" in str(failure)
    assert upload_error(user_id, failure) == FOLDER_MISSING


def test_anything_else_says_drive_did_not_answer_never_its_text(drive, tmp_path):
    user_id = _connected()
    drive.fail_on("clone.mp4")

    failure = _upload_failure(user_id, _folder().id, _video(tmp_path))

    assert "fake outage" in str(failure)
    assert upload_error(user_id, failure) == NO_ANSWER
    assert upload_error(user_id, SignInError("Google sign-in is not set up")) == (
        NO_ANSWER
    )
