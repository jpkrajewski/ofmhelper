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
    assert r.headers["location"] == "/"


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
    assert (r.status_code, r.headers["location"]) == (303, "/reels")
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
def test_google_keeps_the_drive_token(client):
    user = make_user("friend@example.com")
    _consent(client, "google", "friend@example.com")
    assert users.get_user(user.id).drive_connected
    assert users.refresh_token(user.id) == "fake-refresh:friend@example.com"


@pytest.mark.usefixtures("providers_on")
def test_a_google_sign_in_without_a_drive_token_asks_consent_once_more(client):
    """Google waves an account that consented before through without a
    refresh token: signed in, then straight back for consent, ending
    signed in with Drive connected -- no second sign-in by hand."""
    from ofmhelpers.signin.fake import FakeAccount, fake_code

    user = make_user("friend@example.com")
    started = client.get(
        "/auth/google/start", params={"next": "/drive"}, follow_redirects=False
    )
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    nonce = client.cookies.get("ofm_signin")
    assert nonce  # the round trip's cookie
    # The state cookie carries the nonce: read it the way the app does.
    from ofmhelpers.web.routers.auth import _serializer

    carried = _serializer().loads(nonce)
    code = fake_code(
        FakeAccount(
            email="friend@example.com", nonce=carried["nonce"], refresh_token=False
        )
    )
    r = client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/auth/google/start?link=1&next=/drive"
    assert client.get("/generate").status_code == 200  # already signed in
    assert not users.get_user(user.id).drive_connected

    r = _consent(client, "google", "friend@example.com", link="1", next="/drive")
    assert users.get_user(user.id).drive_connected


@pytest.mark.usefixtures("providers_on")
def test_a_google_sign_in_with_the_drive_token_on_file_goes_straight_in(client):
    user = make_user("friend@example.com")
    _consent(client, "google", "friend@example.com")
    assert users.get_user(user.id).drive_connected
    other = TestClient(app)
    r = _consent(other, "google", "friend@example.com", next="/reels")
    assert r.headers["location"] == "/reels"


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
def test_unlinking_the_only_way_in_is_refused(client):
    me = sign_in(client)
    _consent(client, "google", "my.gmail@example.com", link="1")
    assert client.post("/account/unlink/google").status_code == 400
    make_user_with_password(me.email, PASSWORD)
    assert (
        client.post("/account/unlink/google", follow_redirects=False).status_code == 303
    )
    assert users.linked_accounts(me.id) == []
    assert not users.get_user(me.id).drive_connected


def test_login_page_shows_only_configured_providers(client, monkeypatch):
    assert "Continue with" not in client.get("/login").text
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")
    html = client.get("/login").text
    assert "Continue with Google" in html
    assert "Continue with Apple" not in html


def test_google_consent_url_asks_for_drive_and_a_nonce(client, monkeypatch):
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APP_BASE_URL", "https://ascendglobal.cash/")
    r = client.get("/auth/google/start", follow_redirects=False)
    url = urlsplit(r.headers["location"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["redirect_uri"] == "https://ascendglobal.cash/auth/google/callback"
    assert "https://www.googleapis.com/auth/drive" in query["scope"].split()
    assert query["access_type"] == "offline"
    assert query["nonce"]
    assert query["state"]
    assert "ofm_signin=" in r.headers["set-cookie"]


# --- /account: own API keys ----------------------------------------------------


def test_a_users_saved_key_prefills_their_forms_and_nobody_elses(client, monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "env-admin-key")
    sign_in(client)
    assert "env-admin-key" not in client.get("/generate").text

    r = client.post(
        "/account/keys", data={"provider": "kie", "api_key": " user-key-1234 "}
    )
    assert "Key saved." in r.text
    assert "…1234" in r.text
    assert "user-key-1234" in client.get("/generate").text

    other = TestClient(app)
    sign_in(other, "other@example.com")
    assert "user-key-1234" not in other.get("/generate").text

    admin = TestClient(app)
    sign_in_admin(admin)
    assert "env-admin-key" in admin.get("/generate").text

    client.post("/account/keys", data={"provider": "kie", "api_key": ""})
    assert "user-key-1234" not in client.get("/generate").text


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
def test_only_the_first_admin_email_takes_it_over(client):
    """The second listed admin gets an admin account of their own."""
    agency = users.agency_id()
    _consent(client, "google", "second@example.com")
    second = users.by_email("second@example.com")
    assert second.is_admin
    assert second.id != agency
    assert users.by_email("admin") is not None
