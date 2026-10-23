"""Signing in: email + password (and the admin password account), Google and
Apple through their real flow against the in-process fakes of their token
endpoints, set-password links from /users, linking accounts on /account,
and the session following /users changes at once."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from signin import make_user, make_user_with_password, sign_in, sign_in_admin

from ofmhelpers.signin.passwords import hash_password
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import Role
from ofmhelpers.web.stores import users

PASSWORD = "correct horse battery"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _password_login(client: TestClient, email: str, password: str, next_url: str = "/"):
    return client.post(
        "/login",
        data={"email": email, "password": password, "next": next_url},
        follow_redirects=False,
    )


# --- email + password ----------------------------------------------------------


def test_a_user_signs_in_with_their_password(client):
    user = make_user_with_password(password=PASSWORD)
    r = _password_login(client, "  USER@example.com ", PASSWORD, "/clone")
    assert (r.status_code, r.headers["location"]) == (303, "/clone")
    assert client.get("/generate").status_code == 200
    assert users.get_user(user.id).last_login_at is not None


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("user@example.com", "wrong password!!"),
        ("nobody@example.com", PASSWORD),
        ("admin", "not the admin password"),
    ],
)
def test_a_wrong_email_or_password_gets_the_same_answer(client, email, password):
    make_user_with_password(password=PASSWORD)
    r = _password_login(client, email, password)
    assert r.status_code == 401
    assert "Wrong email or password" in r.text
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_the_admin_password_signs_in_as_the_password_account(client):
    r = _password_login(client, "admin", "test-admin")
    assert r.status_code == 303
    admin = users.by_email("admin")
    assert admin is not None
    assert admin.is_admin
    assert client.get("/users").status_code == 200


def test_the_admin_password_opens_an_admin_email_as_that_admin(client, monkeypatch):
    """You type your own Gmail and the admin password: you are in as you."""
    monkeypatch.setenv("OFM_ADMIN_EMAILS", "Owner@Example.com")
    r = _password_login(client, " owner@example.com ", "test-admin")
    assert r.status_code == 303
    owner = users.by_email("owner@example.com")
    assert owner is not None
    assert owner.is_admin
    assert client.get("/users").status_code == 200


def test_the_admin_password_opens_no_other_email(client):
    make_user("someone@example.com")
    assert (
        _password_login(client, "someone@example.com", "test-admin").status_code == 401
    )
    assert (
        _password_login(client, "nobody@example.com", "test-admin").status_code == 401
    )


# --- the app owner: the admin password alone (signin/owner.py) ----------------


def _owner(client: TestClient, password: str, next_url: str = "/"):
    """Start -> the app owner's password page -> its callback."""
    started = client.get(
        "/auth/owner/start", params={"next": next_url}, follow_redirects=False
    )
    assert started.status_code == 303
    page = urlsplit(started.headers["location"])
    assert page.path == "/auth/owner/consent"
    state = parse_qs(page.query)["state"][0]
    sent = client.post(
        "/auth/owner/consent",
        data={"state": state, "password": password},
        follow_redirects=False,
    )
    if sent.status_code != 303:
        return sent
    back = urlsplit(sent.headers["location"])
    query = {k: v[0] for k, v in parse_qs(back.query).items()}
    return client.get(back.path, params=query, follow_redirects=False)


def test_the_login_page_offers_the_app_owner(client):
    assert "Continue with Big G Login" in client.get("/login").text


def test_no_app_owner_without_an_admin_password(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD_ADMIN")
    assert "Big G Login" not in client.get("/login").text


def test_the_admin_password_alone_opens_the_agency_admin(client):
    r = _owner(client, "test-admin", "/links")
    assert (r.status_code, r.headers["location"]) == (303, "/links")
    assert client.get("/users").status_code == 200
    assert users.get_user(users.agency_id()).is_password_account


def test_a_wrong_owner_password_signs_nobody_in_and_is_counted(client, caplog):
    with caplog.at_level("WARNING", logger="ofmhelpers.web.routers.auth"):
        r = _owner(client, "not it")
    assert r.status_code == 401
    assert "Wrong admin password" in r.text
    assert "not it" not in caplog.text
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_an_owner_code_opens_only_its_own_sign_in(client):
    """A code from one sign-in replayed against another (other nonce) is
    refused."""
    from ofmhelpers.signin.owner import owner_code

    started = client.get("/auth/owner/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    r = client.get(
        "/auth/owner/callback",
        params={"code": owner_code("someone-elses-nonce"), "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_the_app_owner_is_never_linked_to_an_account(client):
    sign_in(client)
    assert "Big G Login" not in client.get("/account").text
    assert client.post("/account/unlink/owner").status_code == 404


def test_a_failed_login_logs_the_email_never_the_password(client, caplog):
    with caplog.at_level("WARNING", logger="ofmhelpers.web.routers.auth"):
        _password_login(client, "who@example.com", "secret-typo-123")
    assert "who@example.com" in caplog.text
    assert "secret-typo-123" not in caplog.text


def test_a_deactivated_user_cannot_sign_in(client):
    user = make_user_with_password(password=PASSWORD)
    users.set_active(user.id, active=False)
    assert _password_login(client, user.email, PASSWORD).status_code == 401


@pytest.mark.parametrize(
    "target", ["//evil.example/x", "https://evil.example/", "evil"]
)
def test_after_sign_in_only_a_path_on_this_site(client, target):
    make_user_with_password(password=PASSWORD)
    r = _password_login(client, "user@example.com", PASSWORD, target)
    assert r.headers["location"] == "/home"


@pytest.mark.parametrize("target", ["", "/"])
def test_a_sign_in_without_a_page_to_return_to_lands_on_home(client, target):
    """ "/" is the public landing page; the app starts at /home."""
    make_user_with_password(password=PASSWORD)
    r = _password_login(client, "user@example.com", PASSWORD, target)
    assert (r.status_code, r.headers["location"]) == (303, "/home")


@pytest.mark.parametrize("signs_in", [sign_in, sign_in_admin])
def test_signed_in_the_landing_page_sends_you_home(client, signs_in):
    signs_in(client)
    for path in ("/", "/?applied=1"):
        r = client.get(path, follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (303, "/home"), path


@pytest.mark.usefixtures("providers_on")
def test_a_provider_sign_in_without_a_page_lands_on_home(client):
    make_user("friend@example.com")
    r = _consent(client, "apple", "friend@example.com")
    assert (r.status_code, r.headers["location"]) == (303, "/home")


# --- the session follows /users ------------------------------------------------


def test_a_deactivated_or_removed_user_is_out_at_the_next_click(client):
    user = sign_in(client)
    assert client.get("/generate").status_code == 200
    users.set_active(user.id, active=False)
    r = client.get("/generate", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login?next=/generate")

    users.set_active(user.id, active=True)
    sign_in(client)
    users.delete_user(user.id)
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_a_role_change_applies_at_once(client):
    user = sign_in(client)
    assert client.get("/users").status_code == 403
    users.set_role(user.id, Role.ADMIN)
    assert client.get("/users").status_code == 200


def test_a_session_from_before_users_must_sign_in_again(client):
    """The old cookie held only a role."""
    import json
    from base64 import b64encode

    from itsdangerous import TimestampSigner

    from ofmhelpers.config import settings

    old = b64encode(json.dumps({"authenticated": True, "role": "admin"}).encode())
    cookie = TimestampSigner(settings.session.session_secret).sign(old).decode()
    client.cookies.set("ofm_session", cookie)
    assert client.get("/generate", follow_redirects=False).status_code == 303


# --- /users and set-password links ---------------------------------------------


def _link(html: str) -> str:
    found = re.search(r'value="(http[^"]*/password/[^"]+)"', html)
    assert found, "no password link on the page"
    return urlsplit(found.group(1)).path


def test_an_admin_adds_a_user_who_sets_a_password_from_the_link(client):
    sign_in_admin(client)
    r = client.post("/users", data={"email": " New@Example.com ", "role": "user"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    link = _link(r.text)

    newcomer = TestClient(app)
    form = newcomer.get(link)
    assert form.status_code == 200
    assert "new@example.com" in form.text
    short = newcomer.post(link, data={"password": "short", "confirm": "short"})
    assert short.status_code == 400
    assert "at least 10" in short.text
    differ = newcomer.post(link, data={"password": PASSWORD, "confirm": PASSWORD + "x"})
    assert "differ" in differ.text
    done = newcomer.post(
        link, data={"password": PASSWORD, "confirm": PASSWORD}, follow_redirects=False
    )
    assert done.status_code == 303
    assert newcomer.get("/generate").status_code == 200

    # The link works once.
    assert TestClient(app).get(link).status_code == 404
    assert (
        _password_login(TestClient(app), "new@example.com", PASSWORD).status_code == 303
    )


def test_a_new_link_voids_the_old_one_and_links_expire(client, monkeypatch):
    sign_in_admin(client)
    user = make_user("late@example.com")
    first = _link(client.post(f"/users/{user.id}/link").text)
    second = _link(client.post(f"/users/{user.id}/link").text)
    assert TestClient(app).get(first).status_code == 404
    assert TestClient(app).get(second).status_code == 200

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 8 * 86400)
    assert TestClient(app).get(second).status_code == 404


def test_adding_an_existing_or_bad_email_says_so(client):
    sign_in_admin(client)
    make_user("taken@example.com")
    assert client.post("/users", data={"email": "taken@example.com"}).status_code == 409
    assert client.post("/users", data={"email": "not an email"}).status_code == 400


def test_an_admin_cannot_lock_themselves_out(client):
    me = sign_in_admin(client)
    for action, data in (
        ("role", {"role": "user"}),
        ("active", {"active": "false"}),
        ("delete", {}),
    ):
        assert client.post(f"/users/{me.id}/{action}", data=data).status_code == 400
    assert users.get_user(me.id).is_admin


def test_users_page_is_admins_only(client):
    sign_in(client)
    assert client.get("/users").status_code == 403
    assert client.post("/users", data={"email": "x@example.com"}).status_code == 403


def test_an_admin_changes_role_deactivates_and_removes(client):
    sign_in_admin(client)
    user = make_user("someone@example.com")
    client.post(f"/users/{user.id}/role", data={"role": "admin"})
    assert users.get_user(user.id).is_admin
    client.post(f"/users/{user.id}/active", data={"active": "false"})
    assert not users.get_user(user.id).active
    client.post(f"/users/{user.id}/delete")
    assert users.get_user(user.id) is None


# --- Google and Apple ----------------------------------------------------------


@pytest.fixture
def providers_on(fake_externals, monkeypatch):
    """Both providers configured; their token endpoints answered by
    signin/fake.py, their consent screens by /auth/fake-consent."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    for name, value in {
        "OFM_GOOGLE_CLIENT_ID": "google-client",
        "OFM_GOOGLE_CLIENT_SECRET": "google-secret",
        "OFM_APPLE_SERVICES_ID": "cash.ascendglobal.web",
        "OFM_APPLE_TEAM_ID": "TEAM123456",
        "OFM_APPLE_KEY_ID": "KEY1234567",
        "OFM_APPLE_PRIVATE_KEY": pem.replace("\n", "\\n"),
        "OFM_ADMIN_EMAILS": "Owner@Example.com, second@example.com",
    }.items():
        monkeypatch.setenv(name, value)


def _consent(client: TestClient, provider: str, email: str, **start):
    """Start -> fake consent -> the provider's callback (Apple: POST)."""
    started = client.get(
        f"/auth/{provider}/start", params=start, follow_redirects=False
    )
    assert started.status_code == 303
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    allowed = client.post(
        "/auth/fake-consent",
        data={"provider": provider, "state": state, "email": email},
        follow_redirects=False,
    )
    back = urlsplit(allowed.headers["location"])
    query = {k: v[0] for k, v in parse_qs(back.query).items()}
    if provider == "apple":
        return client.post(back.path, data=query, follow_redirects=False)
    return client.get(back.path, params=query, follow_redirects=False)


@pytest.mark.usefixtures("providers_on")
@pytest.mark.parametrize("provider", ["google", "apple"])
def test_an_added_email_signs_in_with_a_provider(client, provider):
    user = make_user("friend@example.com")
    r = _consent(client, provider, "friend@example.com", next="/reels")
    assert r.status_code == 303
    # Google goes on to connect Drive first (no token yet), Apple straight in.
    assert r.headers["location"] in (
        "/reels",
        "/auth/google/start?link=1&next=%2Freels",
    )
    assert client.get("/generate").status_code == 200
    linked = users.linked_accounts(user.id)
    assert [a.provider for a in linked] == [provider]


@pytest.mark.usefixtures("providers_on")
@pytest.mark.parametrize("provider", ["google", "apple"])
def test_an_email_nobody_added_is_refused(client, provider):
    r = _consent(client, provider, "stranger@example.com")
    assert r.status_code == 403
    assert "has no access" in r.text
    assert users.by_email("stranger@example.com") is None


@pytest.mark.usefixtures("providers_on")
def test_an_env_admin_gets_in_on_a_fresh_install_and_stays_admin(client):
    r = _consent(client, "google", "owner@example.com")
    assert r.status_code == 303
    owner = users.by_email("owner@example.com")
    assert owner is not None
    assert owner.is_admin
    users.set_role(owner.id, Role.USER)
    _consent(TestClient(app), "google", "owner@example.com")
    assert users.by_email("owner@example.com").is_admin


@pytest.mark.usefixtures("providers_on")
def test_a_google_sign_in_without_drive_goes_straight_on_to_connecting_it(client):
    """Prod, 2026-10-22: the VA signed in with the admin Google account and
    then had to find "Connect Google Drive". Sign-in asks only who you are;
    an account without a Drive token is sent on to the Drive consent at
    once, then lands where it was going. With the token on file, a later
    sign-in goes straight in."""
    user = make_user("friend@example.com")

    r = _consent(client, "google", "friend@example.com", next="/reels")
    assert r.headers["location"] == "/auth/google/start?link=1&next=%2Freels"
    assert client.get("/generate").status_code == 200  # signed in already
    assert not users.get_user(user.id).drive_connected

    r = _consent(client, "google", "friend@example.com", link="1", next="/reels")
    assert r.headers["location"] == "/reels"  # back where it was going
    assert users.get_user(user.id).drive_connected
    assert users.refresh_token(user.id) == "fake-refresh:friend@example.com"

    again = TestClient(app)
    r = _consent(again, "google", "friend@example.com", next="/reels")
    assert r.headers["location"] == "/reels"  # Drive on file: no detour
    assert users.get_user(user.id).drive_connected


@pytest.mark.usefixtures("providers_on")
def test_an_apple_sign_in_never_detours_to_drive(client):
    make_user("friend@example.com")
    r = _consent(client, "apple", "friend@example.com", next="/reels")
    assert r.headers["location"] == "/reels"


def test_googles_consent_url_asks_drive_only_when_connecting(monkeypatch):
    from ofmhelpers.signin.google import Google

    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")

    def query(*, connect: bool) -> dict[str, str]:
        url = Google().consent_url("https://x/cb", "s", "n", connect=connect)
        return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}

    sign_in_query = query(connect=False)
    assert sign_in_query["scope"] == "openid email profile"
    assert "access_type" not in sign_in_query
    assert sign_in_query["prompt"] == "select_account"
    connect_query = query(connect=True)
    assert connect_query["scope"].split() == [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/drive",
    ]
    assert (connect_query["access_type"], connect_query["prompt"]) == (
        "offline",
        "consent",
    )


@pytest.mark.usefixtures("providers_on")
def test_a_forged_or_stale_callback_is_refused(client):
    make_user("friend@example.com")
    r = client.get(
        "/auth/google/callback",
        params={"code": "fake:anything", "state": "never-started"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert client.get("/generate", follow_redirects=False).status_code == 303


@pytest.mark.usefixtures("providers_on")
def test_a_reply_with_another_nonce_is_refused(client):
    """A code minted for another sign-in (replayed) carries its nonce."""
    from ofmhelpers.signin.fake import FakeAccount, fake_code

    make_user("friend@example.com")
    started = client.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    code = fake_code(FakeAccount(email="friend@example.com", nonce="someone-elses"))
    r = client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "did not match" in r.text


@pytest.mark.usefixtures("providers_on")
def test_account_links_google_for_drive_and_refuses_a_taken_one(client):
    me = sign_in(client)
    r = _consent(client, "google", "my.gmail@example.com", link="1", next="/account")
    assert r.headers["location"] == "/account?google=linked"
    assert users.get_user(me.id).drive_connected
    assert "Google account linked" in client.get(r.headers["location"]).text

    other = TestClient(app)
    sign_in(other, "other@example.com")
    r = _consent(other, "google", "my.gmail@example.com", link="1")
    assert r.headers["location"] == "/account?google=taken"


@pytest.mark.usefixtures("providers_on")
def test_a_failed_connect_goes_back_to_account_still_signed_in(client):
    me = sign_in(client)
    started = client.get(
        "/auth/google/start", params={"link": "1"}, follow_redirects=False
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    client.cookies.delete("ofm_signin")  # back on a host that never saw it
    r = client.get(
        "/auth/google/callback",
        params={"code": "x", "state": state},
        follow_redirects=False,
    )
    assert (r.status_code, r.headers["location"]) == (303, "/account?google=failed")
    page = client.get(r.headers["location"])
    assert page.status_code == 400
    assert "Connecting your Google account failed" in page.text
    assert not users.get_user(me.id).drive_connected


def test_a_drive_connect_started_on_www_keeps_its_state_for_the_callback(
    monkeypatch,
):
    """Prod, 2026-10-22: started on www., the state cookie stayed there while
    Google sent the person back to APP_BASE_URL's host."""
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APP_BASE_URL", "https://ascendglobal.cash/")
    www = TestClient(app, base_url="https://www.ascendglobal.cash")
    sign_in(www)
    moved = www.get("/auth/google/start", params={"link": "1"}, follow_redirects=False)
    assert moved.status_code == 301
    assert urlsplit(moved.headers["location"]).netloc == "ascendglobal.cash"
    started = www.get(moved.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(started.headers["location"]).query)
    callback = urlsplit(query["redirect_uri"][0])
    assert callback.netloc == "ascendglobal.cash"
    assert www.cookies.get("ofm_signin", domain="ascendglobal.cash")


@pytest.mark.usefixtures("providers_on")
def test_unlinking_the_only_way_in_is_refused(client):
    me = sign_in(client)
    _consent(client, "google", "my.gmail@example.com", link="1")
    assert client.post("/account/unlink/google").status_code == 400
    make_user_with_password(me.email, PASSWORD)
    sign_in(client)  # a new password signs every session out
    assert (
        client.post("/account/unlink/google", follow_redirects=False).status_code == 303
    )
    assert users.linked_accounts(me.id) == []
    assert not users.get_user(me.id).drive_connected


def test_login_page_shows_only_configured_providers(client, monkeypatch):
    assert "Continue with Google" not in client.get("/login").text
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")
    html = client.get("/login").text
    assert "Continue with Google" in html
    assert "Continue with Apple" not in html


def test_google_sign_in_url_has_the_callback_and_a_nonce_but_no_drive(
    client, monkeypatch
):
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APP_BASE_URL", "https://ascendglobal.cash/")
    r = client.get("/auth/google/start", follow_redirects=False)
    url = urlsplit(r.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["redirect_uri"] == "https://ascendglobal.cash/auth/google/callback"
    assert query["scope"] == "openid email profile"
    assert query["nonce"]
    assert query["state"]
    assert "ofm_signin=" in r.headers["set-cookie"]


# --- /account: own API keys ----------------------------------------------------


def test_a_users_saved_key_prefills_their_forms_and_nobody_elses(client, monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "env-admin-key")
    sign_in(client)
    assert "env-admin-key" not in client.get("/generate").text

    r = client.post("/account/keys", data={"key_kie": " user-key-1234 "})
    assert "kie.ai key saved." in r.text
    assert "…1234" in r.text
    assert "user-key-1234" in client.get("/generate").text

    other = TestClient(app)
    sign_in(other, "other@example.com")
    assert "user-key-1234" not in other.get("/generate").text

    admin = TestClient(app)
    sign_in_admin(admin)
    assert "env-admin-key" in admin.get("/generate").text

    client.post("/account/keys", data={"remove": "kie"})
    assert "user-key-1234" not in client.get("/generate").text


def test_one_save_stores_every_key_typed_and_keeps_the_blank_ones(client):
    """Both keys typed, one click: both saved. A blank field later leaves its
    saved key alone; only Remove removes."""
    user = sign_in(client)

    r = client.post(
        "/account/keys",
        data={"key_kie": "kie-key-1111", "key_higgsfield": "hf-key-2222"},
    )
    assert "kie.ai and Higgsfield keys saved." in r.text
    assert users.api_keys(user.id) == {
        "kie": "kie-key-1111",
        "higgsfield": "hf-key-2222",
    }

    r = client.post(
        "/account/keys", data={"key_kie": "kie-key-3333", "key_higgsfield": ""}
    )
    assert users.api_keys(user.id) == {
        "kie": "kie-key-3333",
        "higgsfield": "hf-key-2222",
    }

    r = client.post("/account/keys", data={"key_kie": "", "key_higgsfield": ""})
    assert r.status_code == 400
    assert "Nothing to save" in r.text

    client.post("/account/keys", data={"remove": "higgsfield", "key_kie": ""})
    assert users.api_keys(user.id) == {"kie": "kie-key-3333"}
    assert client.post("/account/keys", data={"remove": "nope"}).status_code == 404


def test_account_password_change_needs_the_current_one(client):
    user = make_user_with_password(password=PASSWORD)
    sign_in(client)
    wrong = client.post(
        "/account/password",
        data={
            "current": "nope",
            "password": "a brand new pass",
            "confirm": "a brand new pass",
        },
    )
    assert "current password is wrong" in wrong.text
    client.post(
        "/account/password",
        data={
            "current": PASSWORD,
            "password": "a brand new pass",
            "confirm": "a brand new pass",
        },
    )
    assert (
        _password_login(TestClient(app), user.email, "a brand new pass").status_code
        == 303
    )


# --- the agency's admin takes over the password account ------------------------------


@pytest.mark.usefixtures("providers_on")
def test_the_first_admin_email_takes_over_the_password_account_and_its_data(client):
    """Prod before users: everything belonged to "admin". The first
    OFM_ADMIN_EMAILS address signing in with Google becomes that account --
    same id, so every phone, account and job is theirs, nothing moved."""
    from ofmhelpers.web.schemas import DeviceFields
    from ofmhelpers.web.stores import devices

    agency = users.agency_id()
    phone = devices.add_device(DeviceFields(label="Agency 01"))

    r = _consent(client, "google", "owner@example.com")

    assert r.status_code == 303
    owner = users.by_email("owner@example.com")
    assert owner.id == agency
    assert owner.is_password_account
    assert owner.is_admin
    assert users.by_email("admin") is None
    assert any(d.id == phone for d in devices.list_devices(owner.id))
    # The admin password still opens it, typed as "admin" or as the email.
    assert _password_login(TestClient(app), "admin", "test-admin").status_code == 303
    assert (
        _password_login(TestClient(app), "owner@example.com", "test-admin").status_code
        == 303
    )
    assert users.agency_id() == agency


@pytest.mark.usefixtures("providers_on")
def test_every_admin_way_in_is_the_one_agency_admin(client):
    """Google with any admin address, an admin address + the admin
    password, the app owner: one user (user decision 2026-10-22)."""
    agency = users.agency_id()
    _consent(client, "google", "second@example.com")
    assert _signed_in_as(client) == agency
    assert users.by_email("second@example.com") is None
    for way_in in (
        lambda c: _consent(c, "google", "owner@example.com"),
        lambda c: _password_login(c, "second@example.com", "test-admin"),
        lambda c: _password_login(c, "admin", "test-admin"),
        lambda c: _owner(c, "test-admin"),
    ):
        other = TestClient(app)
        assert way_in(other).status_code == 303
        assert _signed_in_as(other) == agency
    assert [u.id for u in users.list_users() if u.is_admin] == [agency]


def _signed_in_as(client: TestClient) -> int:
    """The user id `client`'s session is signed in as (/account shows it)."""
    page = client.get("/account").text
    email = page.split('<p class="lead">', 1)[1].split(" · ", 1)[0]
    found = users.by_email(email)
    assert found is not None
    return found.id


# --- review fixes: races, sessions after a password change, escaping -----------


def _together(count: int, call):
    """`call()` from `count` threads released at the same instant."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(count)

    def run():
        barrier.wait()
        return call()

    with ThreadPoolExecutor(count) as pool:
        return [f.result() for f in [pool.submit(run) for _ in range(count)]]


def test_concurrent_adds_of_one_email_make_one_user_and_never_raise():
    repo = users._repository()
    found = _together(8, lambda: repo.add("race@example.com", "user"))
    made = [f for f in found if f is not None]
    assert len(made) == 1
    assert made[0]["email"] == "race@example.com"
    assert [u.email for u in users.list_users()].count("race@example.com") == 1


def test_concurrent_links_of_one_account_go_to_one_user_only():
    first = make_user("one@example.com")
    second = make_user("two@example.com")
    repo = users._repository()
    results = _together(
        8,
        lambda: (
            repo.link_identity(first.id, "google", "sub-race", "one@example.com"),
            repo.link_identity(second.id, "google", "sub-race", "two@example.com"),
        ),
    )
    owner = repo.user_for_identity("google", "sub-race")
    assert owner in {first.id, second.id}
    winner_won = [r[0] if owner == first.id else r[1] for r in results]
    loser_won = [r[1] if owner == first.id else r[0] for r in results]
    assert all(winner_won)
    assert not any(loser_won)


def test_concurrent_first_sign_ins_of_an_admin_email_all_get_in(monkeypatch):
    """The second OFM_ADMIN_EMAILS address, signing in from several tabs at
    once: every tab is the agency admin."""
    from ofmhelpers.signin import Identity

    monkeypatch.setenv("OFM_ADMIN_EMAILS", "owner@example.com, racer@example.com")
    identity = Identity(
        provider="google",
        subject="sub-racer",
        email="racer@example.com",
        email_verified=True,
    )
    signed = _together(6, lambda: users.sign_in_identity(identity))
    assert all(u is not None for u in signed)
    assert {u.id for u in signed} == {users.agency_id()}
    assert [a.subject for a in users.linked_accounts(users.agency_id())] == [
        "sub-racer"
    ]


def test_a_password_change_signs_out_every_other_session(client, caplog):
    make_user_with_password(password=PASSWORD)
    other = TestClient(app)
    assert _password_login(client, "user@example.com", PASSWORD).status_code == 303
    assert _password_login(other, "user@example.com", PASSWORD).status_code == 303
    assert other.get("/generate").status_code == 200

    r = client.post(
        "/account/password",
        data={
            "current": PASSWORD,
            "password": "a brand new pass",
            "confirm": "a brand new pass",
        },
    )

    assert r.status_code == 200
    assert "Password saved" in r.text
    assert client.get("/generate").status_code == 200
    with caplog.at_level("WARNING", logger="ofmhelpers.web.middleware.auth"):
        gone = other.get("/generate", follow_redirects=False)
    assert (gone.status_code, gone.headers["location"]) == (
        303,
        "/login?next=/generate",
    )
    assert "refused (password changed)" in caplog.text


def test_a_password_set_from_a_link_signs_out_the_old_sessions(client):
    user = make_user_with_password(password=PASSWORD)
    assert _password_login(client, user.email, PASSWORD).status_code == 303
    link = f"/password/{users.password_link(user.id)}"

    done = TestClient(app).post(
        link,
        data={"password": "a brand new pass", "confirm": "a brand new pass"},
        follow_redirects=False,
    )

    assert done.status_code == 303
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_an_email_with_a_quote_never_reaches_inline_script(client):
    sign_in_admin(client)
    evil = "x');alert(1);('@example.com"
    make_user(evil)
    page = client.get("/users")
    assert page.status_code == 200
    assert "onsubmit" not in page.text
    assert 'data-confirm="Remove x&#39;);alert(1);(&#39;@example.com?' in page.text


@pytest.mark.usefixtures("providers_on")
def test_connecting_google_drive_syncs_that_persons_folders_right_away(
    client, monkeypatch
):
    """No wait for the next sweep: the Drive token arriving builds the root
    and account folders at once, so /clone's upload works straight away."""
    from ofmhelpers.web import drive_folders

    synced: list[int | None] = []
    monkeypatch.setattr(drive_folders, "sync", synced.append)
    user = make_user("friend@example.com")
    apple = make_user("apple@example.com")

    _consent(client, "google", "friend@example.com")  # sign-in: no Drive yet
    assert synced == []
    _consent(client, "google", "friend@example.com", link="1")
    _consent(TestClient(app), "apple", "apple@example.com")  # no Drive token

    assert synced == [user.id]
    assert apple.id not in synced


# --- ultrareview fixes ------------------------------------------------------


def test_a_deactivated_agency_admin_stays_out_until_the_admin_password(client):
    """A wrong password for "admin" must not restore the deactivated
    account, nor may its own password open it; the admin password does
    (break-glass)."""
    agency = users.agency_id()
    users._repository().set_password_hash(agency, hash_password(PASSWORD))
    users.set_active(agency, active=False)

    assert _password_login(client, "admin", "not it").status_code == 401
    assert not users.get_user(agency).active
    assert _password_login(client, "admin", PASSWORD).status_code == 401
    assert not users.get_user(agency).active

    assert _password_login(client, "admin", "test-admin").status_code == 303
    assert users.get_user(agency).active
