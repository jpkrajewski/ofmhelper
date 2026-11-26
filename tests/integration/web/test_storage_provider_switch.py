# Copyright (C) 2026 jpkrajewski

from types import MappingProxyType

import pytest

from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.drive_folders import store
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import StorageProvider
from ofmhelpers.features.storage import client as storage_client
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage import storage_choice
from ofmhelpers.features.storage.client import _Provider
from ofmhelpers.integrations.storage.gdrive.fake import FakeDrive, fake_drive

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


@pytest.fixture
def dropbox_top() -> FakeDrive:
    top = FakeDrive()
    top.seed_folder("Dropbox", item_id="root", shared=True)
    return top


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path, dropbox_top) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    dropbox = storage_client._providers[StorageProvider.DROPBOX]

    def connect_fake(refresh_token: str, *, interactive: bool) -> FakeDrive:
        assert refresh_token == "dropbox-refresh"
        return dropbox_top

    stub = _Provider(
        token=dropbox.token,
        connect=connect_fake,
        constants=dropbox.constants,
    )
    monkeypatch.setattr(
        storage_client,
        "_providers",
        MappingProxyType({**storage_client._providers, StorageProvider.DROPBOX: stub}),
    )
    return fake


def _live_logins() -> list[int]:
    return [target.login_id for target in store.targets()]


def test_switching_provider_with_stored_folders_and_syncing_loses_no_account(
    drive,
    dropbox_top,
):
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01",
            model_id="iphone-13",
            color_id="blue",
            owner="Anna",
        ),
    )
    added = devices.add_login(
        phone,
        LoginFields(service="instagram", username="ann.ig"),
    )
    assert added is not None
    storage_jobs.sync()
    logins_before = _live_logins()
    accounts_before = list_accounts()
    assert store.account_folders().folders

    owner = users.agency_id()
    storage_accounts.save(
        owner,
        StorageProvider.DROPBOX,
        "dropbox-refresh",
        "dbid:1",
        AGENCY,
    )
    drive.clear_calls()
    storage_choice.use(owner, StorageProvider.DROPBOX)
    assert users.get_user(owner).drive_root_id != ROOT
    assert all(f.root_folder_id != ROOT for f in store.account_folders().folders)
    storage_jobs.sync()

    new_root = users.get_user(owner).drive_root_id
    assert new_root
    assert new_root != ROOT
    assert dropbox_top.item(new_root).parents == ["root"]
    assert drive.calls == []
    assert store.account_folders().folders
    assert _live_logins() == logins_before
    assert list_accounts() == accounts_before
    assert devices.archived_logins() == []
