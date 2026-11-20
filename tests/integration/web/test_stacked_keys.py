import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.db.stores import users
from ofmhelpers.features.chatting import sources
from ofmhelpers.features.chatting.stacked_keys import StackedKeys
from ofmhelpers.integrations.stacked.fake import AGENCY_KEY, CREATOR, CREATOR_KEY
from ofmhelpers.schemas.chatting import ChatPlatform
from ofmhelpers.web.main import app


@pytest.fixture(autouse=True)
def _services(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("STACKED_API_KEY_DANE", raising=False)


def test_an_admin_saves_a_stacked_key_and_its_chats_are_read():
    client = TestClient(app)
    admin = sign_in_admin(client)
    assert 'name="key_stacked"' in client.get("/account").text

    saved = client.post("/account/keys", data={"key_stacked": CREATOR_KEY})

    assert saved.status_code == 200
    assert "Stacked key saved." in saved.text
    assert users.api_keys(admin.id).get("stacked") == CREATOR_KEY
    assert StackedKeys().keys() == [CREATOR_KEY]
    source = sources.source_for(ChatPlatform.STACKED)
    assert source is not None
    assert source.configured()
    assert [m.model_id for m in source.models().found] == [CREATOR]


def test_a_key_stacked_refuses_is_not_saved():
    client = TestClient(app)
    admin = sign_in_admin(client)

    refused = client.post("/account/keys", data={"key_stacked": "stk_live_wrong"})

    assert refused.status_code == 400
    assert "Stacked did not accept that key" in refused.text
    assert users.api_keys(admin.id).get("stacked") == ""


def test_the_env_keys_come_first_and_a_key_comes_once(monkeypatch):
    monkeypatch.setenv("STACKED_API_KEY_AGENCY", AGENCY_KEY)
    client = TestClient(app)
    admin = sign_in_admin(client)
    users.set_api_key(admin.id, "stacked", CREATOR_KEY)
    other = sign_in_admin(TestClient(app), "second@example.com")
    users.set_api_key(other.id, "stacked", AGENCY_KEY)

    assert StackedKeys().keys() == [AGENCY_KEY, CREATOR_KEY]


def test_a_user_has_no_stacked_slot_and_cannot_save_one():
    client = TestClient(app)
    user = sign_in(client, "plain@example.com")

    assert 'name="key_stacked"' not in client.get("/account").text
    answer = client.post("/account/keys", data={"key_stacked": CREATOR_KEY})

    assert answer.status_code == 404
    assert users.api_keys(user.id).get("stacked") == ""
    assert StackedKeys().keys() == []
