"""
The shared page chrome from base.html: the bits that are easy to drop in a
redesign and only show up as a broken phone layout or a lost accessibility
point -- viewport meta, skip link, the labelled nav toggle, and
aria-current on the active nav item.
"""

import os
import re

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_every_page_ships_the_responsive_accessible_chrome(client):
    html = client.get("/todo").text

    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert 'class="skip-link" href="#main"' in html
    assert 'id="main"' in html
    assert 'aria-label="Toggle navigation"' in html


def test_active_nav_item_is_marked_for_assistive_tech(client):
    html = client.get("/models").text

    # Exactly one link is current, and it is the Models one.
    assert html.count('aria-current="page"') == 1
    assert re.search(r'<a href="/models"\s+aria-current="page"', html)


def test_login_page_is_standalone_but_still_responsive():
    html = TestClient(app).get("/login").text

    assert '<html lang="en">' in html
    assert 'name="viewport"' in html
    assert 'action="/login"' in html
