"""The public bridge served on our own link domains (web/middleware/bridge.py):
an 18+ gate with the destination inlined, a click beacon, byte-identical to a
crawler, and inert on every other host."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import make_user
from sqlalchemy import select

from ofmhelpers.db.models import SelfLinkClickRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.features.links import keys as link_keys
from ofmhelpers.integrations.short_links import NewLink
from ofmhelpers.schemas.links import PlannedLink
from ofmhelpers.web.main import app

HOST = "mybridge.test"
DEST = "https://onlyfans.com/dany"
IG_UA = "Mozilla/5.0 (iPhone) Instagram 300.0"
CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"


def _seed_link(slug: str = "dany", destination: str = DEST) -> None:
    user = make_user()
    self_links.add_domain(HOST, None)
    [link_id] = short_links.add_creating(
        user.id,
        "self",
        [
            PlannedLink(
                slug=slug,
                domain=HOST,
                destination=destination,
                target="onlyfans",
                account_id=None,
                creator="",
            )
        ],
    )
    provider = link_keys.client(user.id, "self")
    assert provider is not None
    short_links.created(
        link_id,
        provider.create(NewLink(slug=slug, domain=HOST, destination=destination)),
    )


def _clicks(kind: str | None = None) -> list[SelfLinkClickRow]:
    with session_scope() as s:
        rows = list(s.execute(select(SelfLinkClickRow)).scalars())
        return [r for r in rows if kind is None or r.kind == kind]


@pytest.fixture
def bridge():
    c = TestClient(app, base_url=f"https://{HOST}")
    yield c
    c.close()


def test_bridge_serves_the_gate_with_the_destination_inlined(bridge):
    _seed_link()
    page = bridge.get("/dany", headers={"user-agent": IG_UA})
    assert page.status_code == 200
    assert "Adults Only" in page.text
    assert "Continue (18+)" in page.text
    assert "instagram://extbrowser" in page.text
    # The destination is in the page source (JSON-encoded for the script).
    assert '"https://onlyfans.com/dany"' in page.text
    assert page.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert page.headers["referrer-policy"] == "no-referrer"


def test_the_facebook_app_gets_the_open_in_browser_page(bridge):
    _seed_link()
    fb_ua = "Mozilla/5.0 (iPhone) [FBAN/FBIOS;FBAV/450.0]"
    page = bridge.get("/dany", headers={"user-agent": fb_ua})
    assert page.status_code == 200
    assert "Open in your browser" in page.text
    assert "Adults Only" not in page.text


def test_a_landing_is_counted_once_with_cloudflare_geo(bridge):
    _seed_link()
    bridge.get(
        "/dany",
        headers={
            "user-agent": IG_UA,
            "cf-connecting-ip": "203.0.113.9",
            "cf-ipcountry": "US",
            "cf-ipcity": "Austin",
        },
    )
    views = _clicks("view")
    assert len(views) == 1
    assert views[0].ip == "203.0.113.9"
    assert views[0].country == "US"
    assert views[0].city == "Austin"
    assert views[0].platform == "instagram"
    assert views[0].device == "mobile"
    assert views[0].browser is not None  # computed from the UA at write time


def test_a_chrome_user_agent_is_stored_as_chrome(bridge):
    _seed_link()
    bridge.get("/dany", headers={"user-agent": "Mozilla/5.0 (Android) Chrome/130"})
    assert _clicks("view")[0].browser == "Chrome"


def test_the_beacon_is_rate_limited_per_ip(bridge, monkeypatch):
    monkeypatch.setenv("OFM_LINKS_BRIDGE_RATE_PER_MIN", "3")
    _seed_link()
    for _ in range(6):
        bridge.post(
            "/api/track-direct", json={"slug": "dany"}, headers={"user-agent": IG_UA}
        )
    # Over the cap the beacon still answers 204 but stops recording.
    assert len(_clicks("click")) == 3


def test_the_beacon_records_a_continue_through(bridge):
    _seed_link()
    response = bridge.post(
        "/api/track-direct",
        json={"slug": "dany", "referrer": "https://instagram.com/"},
        headers={"user-agent": IG_UA, "cf-ipcountry": "GB"},
    )
    assert response.status_code == 204
    clicks = _clicks("click")
    assert len(clicks) == 1
    assert clicks[0].referrer == "https://instagram.com/"
    assert clicks[0].country == "GB"


def test_a_crawler_gets_the_same_page_but_is_not_counted(bridge):
    _seed_link()
    page = bridge.get("/dany", headers={"user-agent": CRAWLER_UA})
    assert page.status_code == 200
    assert "Adults Only" in page.text
    assert _clicks("view") == []


def test_an_unknown_slug_is_a_bridge_404(bridge):
    _seed_link()
    page = bridge.get("/nope", headers={"user-agent": IG_UA})
    assert page.status_code == 404
    assert "Link not found" in page.text
    # The 404 is kept off indexes and caches too.
    assert page.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_a_disabled_link_does_not_serve(bridge):
    _seed_link()
    links = short_links.list_links()
    short_links.set_status([links[0].id], "inactive")
    page = bridge.get("/dany", headers={"user-agent": IG_UA})
    assert page.status_code == 404


def test_the_bridge_is_inert_on_a_normal_host():
    # testserver is not a bridge domain: the app handles /dany itself (an
    # unauthenticated app request is sent to login, never the gate).
    _seed_link()
    client = TestClient(app, follow_redirects=False)
    page = client.get("/dany")
    assert "Adults Only" not in page.text
    client.close()
