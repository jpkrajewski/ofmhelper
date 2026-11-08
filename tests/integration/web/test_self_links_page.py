"""/self-links: the Self links page -- its domains (add / disable / remove) and
its click statistics over any time frame, read from the click log."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.models import SelfLinkClickRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.integrations.short_links import NewLink
from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app, follow_redirects=False)
    yield c
    c.close()


def _make_link(user_id: int, slug: str, host: str, dest: str) -> int:
    [link_id] = short_links.add_creating(
        user_id,
        "self",
        [
            {
                "slug": slug,
                "domain": host,
                "destination": dest,
                "target": "onlyfans",
                "account_id": None,
                "creator": "",
            }
        ],
    )
    provider = short_links.client(user_id, "self")
    assert provider is not None
    short_links.created(
        link_id, provider.create(NewLink(slug=slug, domain=host, destination=dest))
    )
    return link_id


def _click(link_id: int, kind: str, when: datetime, **geo: str) -> None:
    with session_scope() as s:
        s.add(SelfLinkClickRow(link_id=link_id, ts=when.timestamp(), kind=kind, **geo))


def test_empty_page_renders_with_its_sections(client):
    sign_in(client)
    page = client.get("/self-links")
    assert page.status_code == 200
    assert "Self links" in page.text
    assert "No domains yet" in page.text
    assert "No self links yet" in page.text


def test_add_then_remove_a_domain(client):
    user = sign_in(client)
    added = client.post("/self-links/domains", data={"host": "mybridge.test"})
    assert added.status_code == 303
    assert "mybridge.test" in client.get("/self-links").text

    # Duplicate is refused.
    again = client.post("/self-links/domains", data={"host": "mybridge.test"})
    assert again.status_code == 400

    domain_id = self_links.domains(user.id)[0].id
    removed = client.post(f"/self-links/domains/{domain_id}/delete")
    assert removed.status_code == 303, removed.text
    assert self_links.domains(user.id) == []
    assert "mybridge.test" not in client.get("/self-links").text


def test_a_bare_url_is_cleaned_to_a_host(client):
    sign_in(client)
    client.post("/self-links/domains", data={"host": "https://Walk.Example.NET/path"})
    assert "walk.example.net" in client.get("/self-links").text


def test_disable_stops_a_domain_serving(client):
    user = sign_in(client)
    client.post("/self-links/domains", data={"host": "bridge.example.com"})
    assert "bridge.example.com" in self_links.active_hosts(user.id)
    domain_id = self_links.domains(user.id)[0].id
    client.post(f"/self-links/domains/{domain_id}/status", data={"status": "disabled"})
    assert "bridge.example.com" not in self_links.active_hosts(user.id)


def test_stats_read_the_click_log_over_a_range(client):
    user = sign_in(client)
    self_links.add_domain("bridge.example.com", user.id)
    link_id = _make_link(
        user.id, "dany", "bridge.example.com", "https://onlyfans.com/dany"
    )

    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    _click(link_id, "view", now, country="US", device="mobile", platform="instagram")
    _click(link_id, "view", now.replace(minute=1), country="US")
    _click(
        link_id,
        "click",
        now.replace(minute=2),
        country="US",
        device="mobile",
        platform="instagram",
    )
    _click(link_id, "click", now - timedelta(days=3), country="GB", device="desktop")

    page = client.get("/self-links?days=7").text
    assert "bridge.example.com/dany" in page
    assert "US" in page  # a country breakdown row
    # Totals: 2 views + 2 clicks over 7 days.
    page30 = client.get("/self-links?days=30")
    assert page30.status_code == 200


def test_custom_from_to_range_is_accepted(client):
    sign_in(client)
    today = datetime.now(UTC).date().isoformat()
    page = client.get(f"/self-links?from={today}&to={today}")
    assert page.status_code == 200
    # A single day is shown by the hour.
    assert "by hour" in page.text
