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
import time
from urllib.parse import parse_qs, urlsplit

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient

from ofmhelpers.config import settings
from ofmhelpers.web.main import app
from ofmhelpers.web.middleware import AuthMiddleware

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
    assert body["login_url"].startswith("/login")


def test_fetch_401_sends_login_back_to_the_calling_page():
    """Prod 2026-10-27..31: the gallery poll's 401 named its own URL, so
    signing in again landed on /clone/gallery/latest, a bare fragment."""
    r = _request(
        "/clone/gallery/latest",
        {"sec-fetch-mode": "cors", "referer": "http://testserver/clone"},
    )

    assert r.status_code == 401
    assert r.json()["login_url"] == "/login?next=/clone"


def test_fetch_401_without_a_calling_page_lands_on_home():
    """No Referer, or another site's: never the fetch URL, login's default."""
    bare = _request("/clone/gallery/latest", {"sec-fetch-mode": "cors"})
    foreign = _request(
        "/clone/gallery/latest",
        {"sec-fetch-mode": "cors", "referer": "https://elsewhere.example/clone"},
    )

    assert bare.json()["login_url"] == "/login"
    assert foreign.json()["login_url"] == "/login"


def test_signing_in_from_a_fetch_401_returns_to_the_page_with_its_query():
    """The whole round trip: 401 -> /login -> sign in -> the calling page,
    its query (several values, an `&`) intact."""
    page = "/clone?video=a.mp4&video=b.mp4"
    r = _request(
        "/clone/gallery/latest",
        {"sec-fetch-mode": "cors", "referer": f"http://testserver{page}"},
    )
    next_url = parse_qs(urlsplit(r.json()["login_url"]).query)["next"][0]

    signed_in = TestClient(app).post(
        "/login",
        data={"password": "test-admin", "next": next_url},
        follow_redirects=False,
    )

    assert (signed_in.status_code, signed_in.headers["location"]) == (303, page)


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
    assert AuthMiddleware.is_fetch(_FakeRequest({"sec-fetch-mode": "cors"})) is True
    assert (
        AuthMiddleware.is_fetch(_FakeRequest({"sec-fetch-mode": "same-origin"})) is True
    )
    assert (
        AuthMiddleware.is_fetch(_FakeRequest({"sec-fetch-mode": "navigate"})) is False
    )
    # No Sec-Fetch-* header: fall back to the older hints.
    assert (
        AuthMiddleware.is_fetch(_FakeRequest({"x-requested-with": "XMLHttpRequest"}))
        is True
    )
    assert AuthMiddleware.is_fetch(_FakeRequest({"accept": "application/json"})) is True
    assert AuthMiddleware.is_fetch(_FakeRequest({"accept": "text/html"})) is False
    assert AuthMiddleware.is_fetch(_FakeRequest({})) is False


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


def test_a_signed_out_download_fails_instead_of_saving_the_login_page():
    """2026-10-23: Download while signed out saved the login page's HTML
    as "VID_8121.mp4", which then "crashed" every player."""
    r = _request(
        "/download-media/files/abc12345/0?dl=1", {"sec-fetch-mode": "navigate"}
    )

    assert r.status_code == 401
    assert r.headers["content-type"].startswith("text/plain")
    assert "<html" not in r.text


def test_a_session_that_ends_says_why(caplog):
    signed_in = TestClient(app)
    signed_in.post("/login", data={"password": "test-admin", "next": "/"})
    signed_in.cookies.set("ofm_session", "garbage")

    with caplog.at_level("WARNING", logger="ofmhelpers.web.middleware.auth"):
        signed_in.get(PROTECTED_PAGE, follow_redirects=False)

    assert "refused (no session)" in caplog.text


# --- the session slides on what the person does -------------------------------

_HOUR = 3600


def _signed_in() -> TestClient:
    person = TestClient(app)
    person.post(
        "/login", data={"password": "test-admin", "next": "/"}, follow_redirects=False
    )
    return person


def _slid(response) -> bool:
    """Whether the answer carried the session cookie again."""
    return "ofm_session=" in response.headers.get("set-cookie", "")


def test_what_the_person_does_slides_the_cookie_and_a_poll_does_not():
    """Starlette re-sends the cookie only when the session is written: the
    middleware writes it on a page opened and on anything that is not a
    read, never on a script's GET."""
    person = _signed_in()

    page = person.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "navigate"})
    poll = person.get("/clone/gallery/latest", headers={"sec-fetch-mode": "cors"})
    kept = person.put("/session", headers={"sec-fetch-mode": "cors"})

    assert (page.status_code, _slid(page)) == (200, True)
    assert (poll.status_code, _slid(poll)) == (200, False)
    assert (kept.status_code, kept.content, _slid(kept)) == (204, b"", True)


def test_keeping_the_session_needs_one():
    r = client.put("/session", headers={"sec-fetch-mode": "cors"})

    assert r.status_code == 401


def test_a_session_lasts_from_the_last_action_not_from_sign_in(monkeypatch):
    """Prod 2026-11: signed out 5 h after sign-in in the middle of work."""
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    person = _signed_in()

    clock[0] += 4 * _HOUR
    person.put("/session", headers={"sec-fetch-mode": "cors"})
    clock[0] += 4 * _HOUR
    r = person.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"})

    assert r.status_code == 200


def test_a_tab_that_only_polls_is_signed_out(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    person = _signed_in()

    clock[0] += 4 * _HOUR
    poll = person.get("/clone/gallery/latest", headers={"sec-fetch-mode": "cors"})
    clock[0] += 2 * _HOUR
    r = person.get("/clone/gallery/latest", headers={"sec-fetch-mode": "cors"})

    assert (poll.status_code, r.status_code) == (200, 401)


# --- Logout ends the session everywhere ----------------------------------------


def test_logout_ends_a_copy_of_the_cookie_and_every_other_device():
    """Found 2026-11-14: the cookie is the whole session, so one copied
    before Logout (stolen, or another device) went on working."""
    here = _signed_in()
    elsewhere = _signed_in()
    stolen = TestClient(app)
    stolen.cookies.set("ofm_session", here.cookies.get("ofm_session"))

    here.post("/logout", follow_redirects=False)
    answers = [
        c.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"}).status_code
        for c in (here, stolen, elsewhere)
    ]

    assert answers == [401, 401, 401]


def test_signing_in_again_after_logout_works(caplog):
    person = _signed_in()
    person.post("/logout", follow_redirects=False)
    copy = TestClient(app)

    again = _signed_in()
    copy.cookies.set("ofm_session", again.cookies.get("ofm_session"))

    assert (
        again.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"}).status_code == 200
    )
    assert (
        copy.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"}).status_code == 200
    )


def test_one_persons_logout_ends_nobody_elses_session():
    from signin import sign_in

    other = TestClient(app)
    sign_in(other, "someone@example.com")
    person = _signed_in()

    person.post("/logout", follow_redirects=False)

    assert (
        other.get(PROTECTED_PAGE, headers={"sec-fetch-mode": "cors"}).status_code == 200
    )
