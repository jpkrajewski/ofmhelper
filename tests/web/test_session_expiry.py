"""
An expired session must be *visible*: leave a tab open past the cookie's
lifetime and every background fetch is unauthenticated, but the UI still
looks signed in. The fix has two halves, both covered here:

- AuthMiddleware answers a fetch/XHR with 401 + login_url (JSON), while a
  real page navigation still gets the 303 redirect to /login. Before this,
  fetch followed the redirect transparently and handed the JS the login
  page's *HTML* with status 200 -- an opaque JSON parse error at best.
- The cookie lifetime the client counts down to comes from config
  (SessionSettings.session_max_age_s), not a hardcoded literal, and reaches
  every page via base.html.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient

from ofmhelpers.config import settings
from ofmhelpers.web.auth import is_fetch
from ofmhelpers.web.main import app

client = TestClient(app)

# A protected JSON endpoint and a protected page -- the two shapes that have
# to diverge for an unauthenticated request.
PROTECTED_JSON = "/todo/1/asset-cell"
PROTECTED_PAGE = "/generate"


def _request(path: str, headers: dict[str, str]):
    # follow_redirects=False so we can see the middleware's own response
    # rather than whatever /login renders.
    return client.get(path, headers=headers, follow_redirects=False)


def test_fetch_gets_401_json_not_a_redirect():
    """The core fix: a fetch()-shaped request gets a machine-readable 401."""
    r = _request(PROTECTED_JSON, {"sec-fetch-mode": "cors"})

    assert r.status_code == 401
    body = r.json()
    assert body["login_url"].startswith("/login?next=")


def test_fetch_401_login_url_preserves_the_target_path():
    """login_url has to send the user back where they were, not just "/"."""
    r = _request("/generate", {"sec-fetch-mode": "cors"})

    assert r.status_code == 401
    assert r.json()["login_url"] == "/login?next=/generate"


def test_page_navigation_still_redirects_to_login():
    """The 401 path must not break the ordinary login bounce -- a browser
    navigating to a protected page still gets a 303, not JSON."""
    r = _request(PROTECTED_PAGE, {"sec-fetch-mode": "navigate"})

    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/generate"


def test_navigation_without_sec_fetch_headers_still_redirects():
    """No Sec-Fetch-* at all (old browser, curl) falls back to a redirect --
    the safe direction, since a redirect is merely unhelpful to a script
    whereas JSON in a browser tab is a broken page."""
    r = _request(PROTECTED_PAGE, {})

    assert r.status_code == 303


def test_authenticated_fetch_is_untouched():
    """Sanity check that the 401 only fires when actually logged out."""
    logged_in = TestClient(app)
    logged_in.post(
        "/login", data={"password": "test-admin", "next": "/"}, follow_redirects=False
    )

    r = logged_in.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"})

    assert r.status_code == 200


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_is_fetch_classification():
    """The navigation-vs-fetch discriminator, at the unit level."""
    assert is_fetch(_FakeRequest({"sec-fetch-mode": "cors"})) is True
    assert is_fetch(_FakeRequest({"sec-fetch-mode": "same-origin"})) is True
    assert is_fetch(_FakeRequest({"sec-fetch-mode": "navigate"})) is False
    # No Sec-Fetch-* header: fall back to the older hints.
    assert is_fetch(_FakeRequest({"x-requested-with": "XMLHttpRequest"})) is True
    assert is_fetch(_FakeRequest({"accept": "application/json"})) is True
    assert is_fetch(_FakeRequest({"accept": "text/html"})) is False
    assert is_fetch(_FakeRequest({})) is False


def test_session_max_age_is_configurable():
    """The lifetime is config, not a literal -- so it can be tuned per
    deployment and the client-side timer can't drift from the cookie."""
    assert settings.session.session_max_age_s == 60 * 60 * 5


def test_pages_expose_the_session_deadline_to_the_client():
    """base.html must hand session.js the deadline on every page, otherwise
    the idle-expiry timer silently never arms."""
    logged_in = TestClient(app)
    logged_in.post(
        "/login", data={"password": "test-admin", "next": "/"}, follow_redirects=False
    )

    body = logged_in.get("/generate").text

    assert f'data-session-max-age="{60 * 60 * 5}"' in body
    assert "/static/js/session.js" in body
