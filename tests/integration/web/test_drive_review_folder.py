"""drive_folders.review_folder: where a reviewed /clone-links video goes --
the owner's root and GOOD/TRIAL REELS in it, found or made one review of a
person at a time (a Redis lock: ten parallel reviews of a first batch made
ten folders of one name, review 2026-10-25 #15), the folder's id remembered
and checked before reuse. Redis down or the lock held too long: the review
goes ahead unlocked."""

import os
import threading
from contextlib import nullcontext, suppress

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from redis.exceptions import RedisError
from signin import make_user

from ofmhelpers.cache import get_redis
from ofmhelpers.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web import drive_folders
from ofmhelpers.web.drive_folders import review_folder
from ofmhelpers.web.stores import users

EMAIL = "ann@example.com"
TOKEN = f"fake-refresh:{EMAIL}"
MY_DRIVE = f"mydrive-{EMAIL}"
ROOT_NAME = "DEV - ALL ACCOUNTS"
GOOD = "GOOD REELS"


@pytest.fixture
def drive(fake_externals) -> FakeDrive:
    return fake_drive()


def _owner() -> int:
    """A VA with Google Drive connected and no root yet (no phones: the
    sweep never made one)."""
    user = make_user(EMAIL)
    users._repository().signed_in(user.id, "", TOKEN)
    return user.id


def _side_by_side(monkeypatch) -> None:
    """Every listing of the fake Drive waits (up to 2 s) for a second review
    to be listing too: without a lock both find nothing, then both make."""
    together = threading.Barrier(2, timeout=2)

    def waiting(read):
        def wrapped(*args, **kwargs):
            found = read(*args, **kwargs)
            with suppress(threading.BrokenBarrierError):
                together.wait()
            return found

        return wrapped

    monkeypatch.setattr(FakeDrive, "children", waiting(FakeDrive.children))
    monkeypatch.setattr(FakeDrive, "find_folder", waiting(FakeDrive.find_folder))


def _two_reviews(user_id: int) -> list[str]:
    """Two reviews of one person at the same moment; their folder ids."""
    start = threading.Barrier(2)
    found: list[str] = []
    errors: list[Exception] = []

    def review() -> None:
        start.wait()
        try:
            found.append(review_folder(TOKEN, user_id, GOOD)[1])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=review) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    return found


def test_two_reviews_at_once_make_one_root_and_one_good_reels(drive, monkeypatch):
    user_id = _owner()
    _side_by_side(monkeypatch)

    found = _two_reviews(user_id)

    (root,) = drive.named(ROOT_NAME, MY_DRIVE)
    (good,) = drive.named(GOOD, root)
    assert found == [good, good]
    assert users.get_user(user_id).drive_root_id == root


def test_without_the_lock_the_same_two_reviews_duplicate(drive, monkeypatch):
    """The harness above catches the race: unlocked, it happens."""
    user_id = _owner()
    _side_by_side(monkeypatch)
    monkeypatch.setattr(drive_folders, "_review_lock", lambda _user_id: nullcontext())

    _two_reviews(user_id)

    assert len(drive.named(ROOT_NAME, MY_DRIVE)) == 2


def test_the_folder_is_remembered_and_checked_with_one_read(drive):
    user_id = _owner()
    _, first = review_folder(TOKEN, user_id, GOOD)
    drive.clear_calls()

    _, again = review_folder(TOKEN, user_id, GOOD)

    assert again == first
    # The root (usable_root) and the remembered folder: one read each.
    assert drive.calls == ["folder", "folder"]


def test_a_remembered_folder_deleted_by_hand_is_made_again(drive):
    user_id = _owner()
    _, first = review_folder(TOKEN, user_id, GOOD)
    drive.trash(first)

    _, again = review_folder(TOKEN, user_id, GOOD)

    root = users.get_user(user_id).drive_root_id
    assert again != first
    assert drive.named(GOOD, root) == [again]


def test_a_remembered_folder_renamed_by_hand_is_not_used(drive):
    user_id = _owner()
    _, first = review_folder(TOKEN, user_id, GOOD)
    drive.rename_by_hand(first, "GOOD REELS (sorted)")

    _, again = review_folder(TOKEN, user_id, GOOD)

    root = users.get_user(user_id).drive_root_id
    assert drive.named(GOOD, root) == [again]
    assert again != first


def test_a_folder_made_by_hand_is_adopted(drive):
    user_id = _owner()
    drive.seed_folder("My Drive", item_id=MY_DRIVE, owner=EMAIL, shared=True)
    root = drive.seed_folder(ROOT_NAME, MY_DRIVE, owner=EMAIL)
    by_hand = drive.seed_folder(GOOD, root, owner=EMAIL)

    _, found = review_folder(TOKEN, user_id, GOOD)

    assert found == by_hand
    assert drive.writes == []


def test_dev_reading_the_live_folders_files_reviews_in_its_own_root(
    drive, monkeypatch, tmp_path
):
    """Live-read mode: dev's own "DEV - ALL ACCOUNTS", writable through
    `ScopedDrive` -- also when the folder id comes back remembered."""
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    user_id = _owner()
    video = tmp_path / "clone.mp4"
    video.write_bytes(b"mp4")

    first_drive, first = review_folder(TOKEN, user_id, GOOD)
    first_drive.upload(first, video, "a.mp4", "video/mp4")
    later_drive, again = review_folder(TOKEN, user_id, GOOD)
    later_drive.upload(again, video, "b.mp4", "video/mp4")

    (root,) = drive.named(ROOT_NAME, MY_DRIVE)
    assert drive.named(GOOD, root) == [first]
    assert again == first
    assert drive.names_in(first) == ["a.mp4", "b.mp4"]
    assert users.get_user(user_id).drive_root_id is None


class _NoRedis:
    def lock(self, *_args, **_kwargs):
        return self

    def acquire(self):
        msg = "Error 111 connecting to redis-test:6379"
        raise RedisError(msg)


def test_redis_down_the_review_goes_ahead_unlocked(drive, monkeypatch):
    user_id = _owner()
    monkeypatch.setattr(drive_folders, "get_redis", _NoRedis)

    _, found = review_folder(TOKEN, user_id, GOOD)

    (root,) = drive.named(ROOT_NAME, MY_DRIVE)
    assert drive.named(GOOD, root) == [found]


def test_a_lock_held_too_long_is_waited_for_then_gone_past(drive, monkeypatch):
    user_id = _owner()
    monkeypatch.setenv("OFM_GDRIVE_REVIEW_LOCK_S", "1")
    held = get_redis().lock(f"lock:drive-review:{user_id}", timeout=30)
    assert held.acquire(blocking=False)

    _, found = review_folder(TOKEN, user_id, GOOD)

    (root,) = drive.named(ROOT_NAME, MY_DRIVE)
    assert drive.named(GOOD, root) == [found]
    held.release()
