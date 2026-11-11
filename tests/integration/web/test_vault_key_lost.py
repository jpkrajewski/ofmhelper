"""A changed or lost `OFM_VAULT_KEY` costs the secrets, never the app: every page
still opens, for a user who has every kind of secret saved. Only what reads a
secret on purpose says so (tests/integration/web/test_devices.py)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from signin import sign_in_admin

from ofmhelpers.db.stores import cookies, users
from ofmhelpers.db.stores import devices as store
from ofmhelpers.features.cookies import jar as cookie_jar
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app

USER = "keeps-secrets@example.com"

PAGES = [
    "/home",
    "/generate",
    "/clone",
    "/clone-long",
    "/clone-links",
    "/download-assets",
    "/accounts",
    "/links",
    "/phones",
    "/drive",
    "/viral",
    "/pictures",
    "/reels",
    "/uniqify",
    "/trials",
    "/self-links",
    "/repurpose",
    "/account",
    "/cookies",
    "/users",
    "/character-sheet",
    "/applications",
    "/file-manager",
    "/styleguide",
    "/action-log",
]

COOKIES = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\tabc\n"


@pytest.fixture
def client() -> TestClient:
    """A user with a Google token, an API key, cookies and an archived login."""
    c = TestClient(app, raise_server_exceptions=False)
    user = sign_in_admin(c, USER)
    users._repository().signed_in(user.id, "", "a-google-refresh-token")
    users.set_api_key(user.id, "kie", "a-provider-key")
    cookie_jar.save(user.id, COOKIES, "cookies.txt")
    device = store.add_device(
        DeviceFields(label="Phone 01", model_id="iphone-13", color_id="blue"),
        user.id,
    )
    added = store.add_login(
        device,
        LoginFields(service="gmail", username="a@b.c", password="secret"),
        user.id,
    )
    assert added is not None
    store.archive_logins([added.id], user.id)
    store.add_login(
        device,
        LoginFields(service="gmail", username="kept@b.c", password="secret"),
        user.id,
    )
    # Nothing left in the cache: the next read is under the changed key.
    users.set_active(user.id, active=True)
    return c


@pytest.fixture(params=["changed", "lost"])
def _key_gone(request, monkeypatch, client):
    if request.param == "changed":
        monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())
    else:
        monkeypatch.delenv("OFM_VAULT_KEY")


@pytest.mark.usefixtures("_key_gone")
@pytest.mark.parametrize("page", PAGES)
def test_every_page_still_opens(client, page):
    r = client.get(page)

    assert r.status_code == 200, r.text[:200]


@pytest.mark.usefixtures("_key_gone")
def test_what_cannot_be_read_counts_as_not_saved(client):
    user = users.by_email(USER)
    assert user is not None

    assert user.drive_connected is True  # the row says so without the key
    assert users.refresh_token(user.id) == ""
    assert users.api_keys(user.id).keys == []
    assert cookies.for_user(user.id) is None
    assert cookie_jar.saved(user.id) is None


@pytest.mark.usefixtures("_key_gone")
def test_a_secret_asked_for_on_purpose_still_says_why(client):
    r = client.get("/phones/export.xlsx")

    assert r.status_code == 500
    assert "OFM_VAULT_KEY" in r.json()["detail"]
