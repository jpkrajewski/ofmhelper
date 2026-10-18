"""/privacy and /terms: public, and say what Google's review looks for."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_public_without_login_and_shows_the_contact(path, monkeypatch):
    monkeypatch.setenv("OFM_CONTACT_EMAIL", "me@example.com")
    response = TestClient(app, follow_redirects=False).get(path)
    assert response.status_code == 200
    assert 'href="mailto:me@example.com"' in response.text


def test_privacy_has_the_google_limited_use_disclosure():
    html = TestClient(app).get("/privacy").text
    assert "Google API Services User Data Policy" in html
    assert "Limited Use requirements" in html
    assert "<code>drive.file</code>" in html


def test_landing_page_links_both():
    html = TestClient(app).get("/").text
    assert 'href="/privacy"' in html
    assert 'href="/terms"' in html
