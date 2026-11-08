"""/privacy and /terms (routers/legal.py): open to anyone signed out -- the
Google OAuth consent screen links to them and its reviewers fetch them
without a session (2026-11-08: both answered 401, the app's verification
stalled for months) -- and linked from the landing page's footer."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web.main import app

LIMITED_USE = (
    "Ascend Global's use and transfer to any other app of information received"
    " from Google APIs will adhere to the"
)


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "title"),
    [("/privacy", "Privacy Policy"), ("/terms", "Terms of Service")],
)
def test_the_legal_pages_open_signed_out(anon, path, title):
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 200
    assert f'<h1 class="rm-legal-title">{title}</h1>' in r.text
    assert 'href="/privacy"' in r.text
    assert 'href="/terms"' in r.text


def test_the_privacy_policy_says_what_google_review_asks_for(anon):
    """Restricted scope: what is accessed and why, the Limited Use
    statement, how it is stored and deleted, a contact."""
    text = anon.get("/privacy").text
    assert "<code>https://www.googleapis.com/auth/drive</code>" in text
    assert LIMITED_USE in text
    assert "https://developers.google.com/terms/api-services-user-data-policy" in text
    assert "Limited Use requirements" in text
    assert "stored encrypted" in text
    assert "Account → Unlink Google" in text
    assert "https://myaccount.google.com/permissions" in text
    assert "https://t.me/ascendonboard" in text


def test_the_contact_email_shows_only_when_set(anon, monkeypatch):
    monkeypatch.setenv("OFM_CONTACT_EMAIL", "")  # outranks a .env value
    for path in ("/privacy", "/terms"):
        assert "mailto:" not in anon.get(path).text

    monkeypatch.setenv("OFM_CONTACT_EMAIL", "privacy@example.com")
    for path in ("/privacy", "/terms"):
        assert 'href="mailto:privacy@example.com"' in anon.get(path).text


def test_the_landing_page_links_both(anon):
    text = anon.get("/").text
    assert '<a href="/privacy" class="rm-footer-link">Privacy Policy</a>' in text
    assert '<a href="/terms" class="rm-footer-link">Terms of Service</a>' in text


def test_the_legal_pages_open_signed_in_too():
    client = TestClient(app)
    sign_in(client)
    assert client.get("/privacy").status_code == 200
    assert client.get("/terms").status_code == 200
