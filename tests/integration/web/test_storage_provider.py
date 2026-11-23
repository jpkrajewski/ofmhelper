from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import StorageProvider
from ofmhelpers.features.storage import client as storage_client
from ofmhelpers.features.storage.constants import StorageConstants
from ofmhelpers.integrations.storage import FolderGoneError
from ofmhelpers.integrations.storage.dropbox import dropbox_constants
from ofmhelpers.web.main import app

EMAIL = "dbx@example.com"
DROPBOX_HOME = "https://www.dropbox.com/home/Reels"
GONE = "id:gone"


class DropboxStub:
    def web_url(self, item_id: str) -> str:
        if item_id == GONE:
            raise FolderGoneError
        return f"{DROPBOX_HOME}/{item_id}"


def _stub_connect(refresh_token: str, *, interactive: bool) -> DropboxStub:
    assert refresh_token == "dropbox-refresh"
    return DropboxStub()


@pytest.fixture
def stubbed_dropbox(fake_externals, monkeypatch):
    dropbox = storage_client._providers[StorageProvider.DROPBOX]
    stub = dropbox.model_copy(update={"connect": _stub_connect})
    monkeypatch.setattr(
        storage_client,
        "_providers",
        MappingProxyType({**storage_client._providers, StorageProvider.DROPBOX: stub}),
    )


def _dropbox_user() -> int:
    user = make_user(EMAIL)
    storage_accounts.save(
        user.id, StorageProvider.DROPBOX, "dropbox-refresh", "dbid:1", EMAIL
    )
    users.set_storage_provider(user.id, StorageProvider.DROPBOX)
    return user.id


def test_a_dropbox_person_gets_a_dropbox_client_and_its_texts(stubbed_dropbox):
    user_id = _dropbox_user()

    access = storage_client.access(user_id, interactive=True)

    assert access.client is not None
    assert access.client.web_url("id:1") == f"{DROPBOX_HOME}/id:1"
    assert access.constants.no_drive == dropbox_constants().no_drive
    assert access.session
    assert access.bot is None


def test_a_dropbox_person_with_no_saved_token_has_no_client(stubbed_dropbox):
    user = make_user(EMAIL)
    users.set_storage_provider(user.id, StorageProvider.DROPBOX)

    access = storage_client.access(user.id, interactive=True)

    assert access.client is None
    assert access.constants.no_drive == dropbox_constants().no_drive


def test_a_google_person_is_unchanged(fake_externals):
    user = make_user(EMAIL)
    users._repository().signed_in(user.id, "", f"fake-refresh:{EMAIL}")

    access = storage_client.access(user.id, interactive=True)

    assert access.client is not None
    assert access.constants == StorageConstants()
    assert storage_client.saved_storage(user.id).constants == StorageConstants()


def test_the_folder_route_redirects_to_the_providers_page(stubbed_dropbox):
    user_id = _dropbox_user()
    client = TestClient(app)
    sign_in(client, EMAIL)

    answer = client.get("/drive/folders/id:abc", follow_redirects=False)

    assert user_id
    assert answer.status_code == 303
    assert answer.headers["location"] == f"{DROPBOX_HOME}/id:abc"


def test_a_gone_folder_goes_back_to_the_drive_page(stubbed_dropbox):
    _dropbox_user()
    client = TestClient(app)
    sign_in(client, EMAIL)

    answer = client.get(f"/drive/folders/{GONE}", follow_redirects=False)

    assert answer.status_code == 303
    assert answer.headers["location"] == "/drive"


def test_a_person_with_no_drive_goes_to_the_account_page(fake_externals):
    client = TestClient(app)
    sign_in(client, EMAIL)

    answer = client.get("/drive/folders/anything12345", follow_redirects=False)

    assert answer.status_code == 303
    assert answer.headers["location"] == "/account"
