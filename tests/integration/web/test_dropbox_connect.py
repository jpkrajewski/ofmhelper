from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from signin import sign_in
from sqlalchemy import select

from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import StorageProvider
from ofmhelpers.features.storage import dropbox_connect
from ofmhelpers.integrations.storage.base import DriveAuthError
from ofmhelpers.web.main import app

EMAIL = "dropbox-user@example.com"
GRANT = SimpleNamespace(refresh_token="refresh-1", account_id="dbid:1")
ME = SimpleNamespace(email="me@dropbox.example", account_id="dbid:1", name="Me")


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("DROPBOX_APP_KEY", "key")
    monkeypatch.setenv("DROPBOX_APP_SECRET", "secret")
    monkeypatch.setattr(dropbox_connect.oauth, "exchange", lambda *_: GRANT)
    monkeypatch.setattr(dropbox_connect.oauth, "account", lambda _: ME)
    c = TestClient(app, follow_redirects=False)
    sign_in(c, EMAIL)
    return c


def _state(client: TestClient) -> str:
    sent = client.get("/account/dropbox/connect")
    return parse_qs(urlsplit(sent.headers["location"]).query)["state"][0]


def _sync_jobs(user_id: int) -> int:
    with session_scope() as session:
        rows = session.scalars(
            select(JobRow).where(JobRow.task == "drive_sync", JobRow.user_id == user_id)
        ).all()
    return len(rows)


def test_connect_sends_the_person_to_dropbox_with_a_state_and_an_s256_challenge(client):
    sent = client.get("/account/dropbox/connect")
    assert sent.status_code == 303
    target = urlsplit(sent.headers["location"])
    query = parse_qs(target.query)
    assert target.netloc == "www.dropbox.com"
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]
    assert query["redirect_uri"][0].endswith("/account/dropbox/callback")


def test_connect_without_an_app_key_goes_back_to_the_account_page(client, monkeypatch):
    monkeypatch.setenv("DROPBOX_APP_KEY", "")
    sent = client.get("/account/dropbox/connect")
    assert sent.headers["location"] == "/account?dropbox=failed"


def test_a_callback_with_an_unknown_state_is_refused_with_a_notice(client):
    back = client.get("/account/dropbox/callback?code=c&state=nope")
    assert back.headers["location"] == "/account?dropbox=failed"
    shown = client.get("/account?dropbox=failed")
    assert "Connecting your Dropbox account failed" in shown.text


def test_a_callback_without_a_code_or_after_a_refusal_goes_back_to_the_page(client):
    state = _state(client)
    back = client.get(f"/account/dropbox/callback?state={state}&error=access_denied")
    assert back.headers["location"] == "/account?dropbox=failed"


def test_a_good_callback_saves_the_account_switches_storage_and_starts_the_sync(client):
    user = users.by_email(EMAIL)
    assert user is not None
    users.set_drive_root(user.id, "old-google-root")
    before = _sync_jobs(user.id)
    state = _state(client)

    back = client.get(f"/account/dropbox/callback?code=c&state={state}")

    assert back.headers["location"] == "/drive"
    account = storage_accounts.get(user.id, StorageProvider.DROPBOX)
    assert account is not None
    assert account.email == "me@dropbox.example"
    assert (
        storage_accounts.refresh_token(user.id, StorageProvider.DROPBOX) == "refresh-1"
    )
    changed = users.get_user(user.id)
    assert changed is not None
    assert changed.storage_provider == StorageProvider.DROPBOX
    assert changed.drive_root_id is None
    assert _sync_jobs(user.id) == before + 1


def test_a_second_connect_clears_the_root_and_queues_another_sync(client):
    user = users.by_email(EMAIL)
    assert user is not None
    client.get(f"/account/dropbox/callback?code=c&state={_state(client)}")
    users.set_drive_root(user.id, "id:root-of-the-first-dropbox")
    before = _sync_jobs(user.id)

    back = client.get(f"/account/dropbox/callback?code=c&state={_state(client)}")

    assert back.headers["location"] == "/drive"
    again = users.get_user(user.id)
    assert again is not None
    assert again.storage_provider == StorageProvider.DROPBOX
    assert again.drive_root_id is None
    assert _sync_jobs(user.id) == before + 1


def test_a_failure_after_the_token_exchange_is_logged_and_sent_back_not_a_500(
    client, monkeypatch, caplog
):
    def broken(*_):
        msg = "database down"
        raise RuntimeError(msg)

    monkeypatch.setattr(dropbox_connect.storage_accounts, "save", broken)
    state = _state(client)

    back = client.get(f"/account/dropbox/callback?code=c&state={state}")

    assert back.status_code == 303
    assert back.headers["location"] == "/account?dropbox=failed"
    assert any(r.exc_info for r in caplog.records)


def test_a_failure_choosing_dropbox_after_it_is_stored_is_not_a_connect(
    client, monkeypatch, caplog
):
    def broken(*_):
        msg = "database down"
        raise RuntimeError(msg)

    monkeypatch.setattr(dropbox_connect.storage_choice, "use", broken)
    state = _state(client)

    back = client.get(f"/account/dropbox/callback?code=c&state={state}")

    assert back.headers["location"] == "/account?dropbox=failed"
    assert any(r.exc_info for r in caplog.records)


def test_a_state_is_good_once_and_only_for_the_person_who_started_it(client):
    state = _state(client)
    assert dropbox_connect.finish(999999, "c", state) is False
    assert (
        client.get(f"/account/dropbox/callback?code=c&state={state}").headers[
            "location"
        ]
        == "/account?dropbox=failed"
    )


def test_a_refused_code_changes_nothing(client, monkeypatch):
    def refused(*_):
        msg = "no"
        raise DriveAuthError(msg)

    monkeypatch.setattr(dropbox_connect.oauth, "exchange", refused)
    user = users.by_email(EMAIL)
    assert user is not None
    users.set_storage_provider(user.id, StorageProvider.GDRIVE)
    state = _state(client)
    back = client.get(f"/account/dropbox/callback?code=c&state={state}")
    assert back.headers["location"] == "/account?dropbox=failed"
    kept = users.get_user(user.id)
    assert kept is not None
    assert kept.storage_provider == StorageProvider.GDRIVE


def test_the_account_page_shows_the_dropbox_card_and_who_is_in_use(client):
    page = client.get("/account").text
    assert "Connect Dropbox" in page
    state = _state(client)
    client.get(f"/account/dropbox/callback?code=c&state={state}")
    page = client.get("/account").text
    assert "Connected as me@dropbox.example" in page
    assert "Reconnect Dropbox" in page
