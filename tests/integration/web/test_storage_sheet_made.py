from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from signin import make_user

from ofmhelpers.config import settings
from ofmhelpers.features.refusals import UnavailableError
from ofmhelpers.features.storage import sheet as sheet_module
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.features.storage.models import StorageAccess
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.integrations.storage.gdrive.fake import (
    FakeDrive,
    fake_drive,
    fake_person,
)

EMAIL = "sheet-maker@example.com"
NAME = "content_links_made"


@pytest.fixture(autouse=True)
def drive(fake_externals) -> FakeDrive:
    return fake_drive()


def _storage() -> tuple[StorageService, int]:
    user = make_user(EMAIL)
    access = StorageAccess(
        owner_id=user.id,
        client=fake_person(f"fake-refresh:{EMAIL}"),
        constants=StorageConstants(),
    )
    return StorageService.compose(access), user.id


def _files_named(drive: FakeDrive, storage: StorageService) -> list[str]:
    root = storage.folders.ensure_root()
    return drive.named(NAME, root)


def test_a_missing_sheet_is_made_with_todays_date_in_a1_and_read_back(drive):
    storage, _ = _storage()
    assert _files_named(drive, storage) == []

    made = storage.sheet.get_by_name(NAME)

    today = datetime.now(ZoneInfo(settings.gdrive.timezone)).date()
    assert made.name == NAME
    assert made.tabs[0].name == NAME
    assert made.tabs[0].rows[0][0] == today
    assert len(_files_named(drive, storage)) == 1


def test_without_the_lock_no_sheet_is_made(drive, monkeypatch):
    @contextmanager
    def not_held(key: str, wait_s: int) -> Iterator[bool]:
        yield False

    monkeypatch.setattr(sheet_module, "lock_held", not_held)
    storage, _ = _storage()

    with pytest.raises(UnavailableError):
        storage.sheet.get_by_name(NAME)

    assert _files_named(drive, storage) == []


def test_the_second_call_reuses_the_sheet_and_makes_no_second_file(drive):
    storage, _ = _storage()

    first = storage.sheet.get_by_name(NAME)
    second = storage.sheet.get_by_name(NAME)

    assert second.tabs == first.tabs
    assert len(_files_named(drive, storage)) == 1
