"""
Covers the admin-only Models page (web/models.py + routers/models.py):
CRUD for a model's name/profile picture/OnlyFans link, plus its nested
Instagram accounts. Every route is admin-only server-side (whole router is
gated via require_admin, like file_manager.py/action_log.py), so a VA must
get a 403 on every endpoint, not just a hidden nav link.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.scraping.instagram_stats_job import collect_all_instagram_stats
from ofmhelpers.web import instagram_stats
from ofmhelpers.web import models as models_store
from ofmhelpers.web.main import app
from ofmhelpers.web.routers import models as models_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def va_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


@pytest.fixture(autouse=True)
def _isolated_pictures(monkeypatch, tmp_path):
    monkeypatch.setattr(models_router, "PICTURE_ROOT", tmp_path / "model_pictures")


def test_va_gets_403_on_every_route(va_client):
    model = models_store.add_model("Model A", "https://onlyfans.com/a")

    assert va_client.get("/models").status_code == 403
    assert va_client.get("/models/new").status_code == 403
    assert va_client.post("/models/add", data={"name": "x"}).status_code == 403
    assert va_client.get(f"/models/{model['id']}/edit").status_code == 403
    assert (
        va_client.post(f"/models/{model['id']}/update", data={"name": "y"}).status_code
        == 403
    )
    assert va_client.post(f"/models/{model['id']}/delete").status_code == 403
    assert (
        va_client.post(
            f"/models/{model['id']}/instagram/add", data={"urls": "https://x"}
        ).status_code
        == 403
    )


def test_new_page_renders_the_add_form(client):
    r = client.get("/models/new")
    assert r.status_code == 200
    assert 'action="/models/add"' in r.text


def test_admin_can_add_model_and_it_appears(client):
    r = client.post(
        "/models/add",
        data={"name": "Model A", "onlyfans_url": "https://onlyfans.com/a"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    html = client.get("/models").text
    assert "Model A" in html

    items = models_store.list_models()
    assert len(items) == 1
    assert items[0]["onlyfans_url"] == "https://onlyfans.com/a"
    assert items[0]["instagram_accounts"] == []


def test_add_rejects_blank_name(client):
    r = client.post("/models/add", data={"name": "   "})
    assert r.status_code == 400
    assert models_store.list_models() == []


def test_add_with_profile_picture_stores_and_serves_it(client):
    files = {"profile_picture": ("pic.png", b"fake image bytes", "image/png")}
    r = client.post(
        "/models/add", data={"name": "Model A"}, files=files, follow_redirects=False
    )
    assert r.status_code == 303

    model = models_store.list_models()[0]
    assert model["profile_picture_name"] == "pic.png"

    r = client.get(f"/models/{model['id']}/picture")
    assert r.status_code == 200
    assert r.content == b"fake image bytes"


def test_admin_can_update_model(client):
    model = models_store.add_model("Model A", "https://onlyfans.com/a")

    r = client.post(
        f"/models/{model['id']}/update",
        data={"name": "Model A2", "onlyfans_url": "https://onlyfans.com/a2"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored = models_store.get_model(model["id"])
    assert stored["name"] == "Model A2"
    assert stored["onlyfans_url"] == "https://onlyfans.com/a2"


def test_update_404s_for_unknown_model(client):
    r = client.post("/models/doesnotexist/update", data={"name": "x"})
    assert r.status_code == 404


def test_admin_can_delete_model(client):
    model = models_store.add_model("Model A", "")

    r = client.post(f"/models/{model['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert models_store.list_models() == []


def test_delete_404s_for_unknown_model(client):
    assert client.post("/models/doesnotexist/delete").status_code == 404


def test_admin_can_add_edit_and_remove_instagram_accounts(client):
    model = models_store.add_model("Model A", "")

    r = client.post(
        f"/models/{model['id']}/instagram/add",
        data={"urls": "https://instagram.com/a"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored = models_store.get_model(model["id"])
    assert len(stored["instagram_accounts"]) == 1
    account = stored["instagram_accounts"][0]
    assert account["url"] == "https://instagram.com/a"

    r = client.post(
        f"/models/{model['id']}/instagram/{account['id']}/update",
        data={"url": "https://instagram.com/a-renamed"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    stored = models_store.get_model(model["id"])
    assert stored["instagram_accounts"][0]["url"] == "https://instagram.com/a-renamed"

    r = client.post(
        f"/models/{model['id']}/instagram/{account['id']}/delete",
        follow_redirects=False,
    )
    assert r.status_code == 303
    stored = models_store.get_model(model["id"])
    assert stored["instagram_accounts"] == []


def test_deleting_model_cascades_to_its_instagram_accounts(client):
    model = models_store.add_model("Model A", "")
    models_store.add_instagram_account(model["id"], "https://instagram.com/a")

    client.post(f"/models/{model['id']}/delete")

    assert models_store.get_model(model["id"]) is None


def test_instagram_add_rejects_blank_urls(client):
    model = models_store.add_model("Model A", "")
    r = client.post(f"/models/{model['id']}/instagram/add", data={"urls": "   \n  "})
    assert r.status_code == 400


def test_instagram_add_404s_for_unknown_model(client):
    r = client.post("/models/doesnotexist/instagram/add", data={"urls": "https://x"})
    assert r.status_code == 404


def test_instagram_bulk_add_accepts_multiple_lines_and_drops_blanks(client):
    model = models_store.add_model("Model A", "")

    r = client.post(
        f"/models/{model['id']}/instagram/add",
        data={"urls": "https://instagram.com/a\n\n  https://instagram.com/b  \n"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored = models_store.get_model(model["id"])
    urls = {a["url"] for a in stored["instagram_accounts"]}
    assert urls == {"https://instagram.com/a", "https://instagram.com/b"}


def test_edit_page_404s_for_unknown_model(client):
    assert client.get("/models/doesnotexist/edit").status_code == 404


def test_list_page_offers_a_manual_refresh_for_all_accounts(client, monkeypatch):
    """The nightly sweep is the normal path; this button is the manual one,
    and it must enqueue the same all-accounts job rather than a per-model one."""
    enqueued = []
    monkeypatch.setattr(
        models_router, "enqueue", lambda fn, *a, **kw: enqueued.append(fn)
    )

    assert 'data-action="/models/refresh-stats"' in client.get("/models").text

    r = client.post("/models/refresh-stats")
    assert r.status_code == 200
    assert enqueued == [collect_all_instagram_stats]
    # Synchronous mode ran it inline, so there is no job to poll.
    assert r.json() == {"job_id": None}


def test_refresh_status_reports_finished_for_an_unknown_job(client):
    """RQ forgets a job after its result TTL; treating that as failed would
    show an error banner for every sweep that finished a while ago."""
    r = client.get("/models/refresh-stats/no-such-job")
    assert r.json() == {"status": "finished", "error": None}


def test_stats_html_returns_just_the_slots_for_in_place_refresh(client):
    """The refresh button swaps these into the page, so each slot has to
    carry the account id it belongs to."""
    model = models_store.add_model("Model A", "")
    models_store.add_instagram_account(model["id"], "https://instagram.com/a")
    account = models_store.get_model(model["id"])["instagram_accounts"][0]
    instagram_stats.save_stats(account["id"], followers=1234, posts=[], error=None)

    html = client.get("/models/stats-html").text

    assert f'data-stats-for="{account["id"]}"' in html
    assert "1,234 followers" in html
    assert "<h1>" not in html  # a fragment, not the whole page


def test_list_page_shows_stats_with_shortened_reel_links(client):
    """Full reel URLs are ~50 chars of repeated prefix -- only the shortcode
    tells two reels apart, so that's what the cell shows."""
    model = models_store.add_model("Model A", "")
    models_store.add_instagram_account(
        model["id"], "https://www.instagram.com/jake_brooks_fd/"
    )
    account = models_store.get_model(model["id"])["instagram_accounts"][0]
    instagram_stats.save_stats(
        account["id"],
        followers=29000,
        posts=[
            {
                "url": "https://www.instagram.com/jake_brooks_fd/reel/DbO0AIYgiRL/",
                "views": 7367,
                "likes": 281,
                "comments": 2,
            }
        ],
        error=None,
    )

    html = client.get("/models").text

    assert "29,000 followers" in html
    assert "/reel/DbO0AIYgiRL<" in html
    assert "@jake_brooks_fd<" in html
    # the full URL stays reachable, just not as the visible label
    assert "https://www.instagram.com/jake_brooks_fd/reel/DbO0AIYgiRL/" in html
    for value in ("7,367", "281"):
        assert value in html


def test_refresh_stats_reports_no_active_sweep_in_sync_mode(client):
    """The page asks this on load to rejoin a sweep that outlived the tab.
    Synchronous mode has no worker and never has one in flight."""
    assert client.get("/models/refresh-stats").json() == {"job_id": None}
