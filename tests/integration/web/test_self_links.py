"""/links with the self provider: a user adds a domain, generates a bridge
link on it (no API key, runs inline), and it shows live on the page. Self is
served from our own data, so there is no wire fake here."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.db.stores.tracked_accounts import list_accounts, track
from ofmhelpers.web.main import app

ONLYFANS = "https://onlyfans.com/danywalking"


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    c.close()


def _accounts(user_id: int, creator: str, *handles: str) -> list[int]:
    for handle in handles:
        track("instagram", handle, creator, user_id=user_id)
    ids = {a.handle: a.id for a in list_accounts(user_id)}
    return [ids[h] for h in handles]


def _generate(client, account_ids, **changes):
    body = {
        "provider": "self",
        "creator": "Dane",
        "target": "onlyfans",
        "destination": ONLYFANS,
        "domain": "mybridge.test",
        "account_ids": account_ids,
        **changes,
    }
    return client.post("/links/generate", json=body)


def test_self_is_always_available_without_an_api_key():
    user = sign_in(TestClient(app))
    states = {s.key: s for s in short_links.provider_states(user.id)}
    assert states["self"].has_key is True


def test_generate_is_refused_until_a_domain_is_added(client):
    user = sign_in(client)
    ids = _accounts(user.id, "Dane", "danywalking")
    response = _generate(client, ids)
    assert response.status_code == 400
    assert "domain" in response.json()["detail"].lower()


def test_generate_makes_a_live_bridge_link(client):
    user = sign_in(client)
    self_links.add_domain("mybridge.test", None)
    ids = _accounts(user.id, "Dane", "danywalking")

    response = _generate(client, ids)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1

    links = short_links.list_links(user.id)
    assert len(links) == 1
    link = links[0]
    assert link.provider == "self"
    assert link.status == "active"
    assert link.url == "https://mybridge.test/danywalking"
    assert link.destination == ONLYFANS
    assert link.adult is True
    assert link.account_id == ids[0]

    page = client.get("/links")
    assert page.status_code == 200
    assert "mybridge.test/danywalking" in page.text
    assert "Self links" in page.text


def test_the_generate_dialog_offers_self_with_its_domain(client):
    sign_in(client)
    self_links.add_domain("mybridge.test", None)
    page = client.get("/links")
    assert page.status_code == 200
    # self is a provider option with its live domain in the domain picker.
    assert '<option value="self"' in page.text
    assert 'value="mybridge.test" data-provider="self"' in page.text
