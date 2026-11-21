import hashlib
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from signin import (
    SESSION_COOKIE,
    make_user,
    make_user_with_password,
    session_cookie,
    sign_in,
)

from ofmhelpers.cache import get_redis
from ofmhelpers.db.models import UserRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, UserForm
from ofmhelpers.features.account import telegram
from ofmhelpers.features.chatting import fanvue_connect
from ofmhelpers.features.refusals import UnavailableError
from ofmhelpers.features.signin import consent, flow
from ofmhelpers.features.signin import state as signin_state
from ofmhelpers.features.signin.outcome import signed_out
from ofmhelpers.features.signin.state import take_verifier, unseal
from ofmhelpers.features.users import credentials
from ofmhelpers.features.users.viewing import signed_in_user
from ofmhelpers.integrations.fanvue import fake as fanvue_fake
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.integrations.senders.telegram.inbox import Start
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.signin import google as google_module
from ofmhelpers.integrations.signin.base import TokenReply
from ofmhelpers.integrations.signin.google import Google
from ofmhelpers.integrations.signin.passwords import verify_password
from ofmhelpers.web.main import app

PASSWORD = "correct horse battery"


def _token(**fields) -> str:
    base = {
        "sub": "s",
        "nonce": "n",
        "iss": "https://accounts.google.com",
        "aud": "google-client",
        "exp": time.time() + 300,
    }
    body = urlsafe_b64encode(json.dumps(base | fields).encode()).decode()
    return f"e30.{body}.sig"


@pytest.fixture
def google_on(monkeypatch):
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "google-secret")


@pytest.mark.usefixtures("google_on")
def test_an_id_token_for_this_app_from_google_is_accepted():
    found = Google().identity(TokenReply(id_token=_token()), "n")
    assert found.subject == "s"


@pytest.mark.usefixtures("google_on")
@pytest.mark.parametrize(
    "wrong",
    [
        {"aud": "another-app"},
        {"aud": ["another-app"]},
        {"aud": ""},
        {"iss": "https://evil.example"},
        {"iss": ""},
        {"exp": 1},
        {"exp": 0},
    ],
)
def test_an_id_token_for_another_app_issuer_or_time_is_refused(wrong):
    with pytest.raises(SignInError):
        Google().identity(TokenReply(id_token=_token(**wrong)), "n")


@pytest.mark.usefixtures("google_on")
def test_google_sign_in_carries_a_pkce_pair_from_start_to_exchange(monkeypatch):
    started = consent.begin(Google(), "http://x/", "/", None)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started.url).query).items()}
    unseal(started.cookie, "google", query["state"])
    verifier = take_verifier(query["state"])
    digest = hashlib.sha256(verifier.encode()).digest()
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == urlsafe_b64encode(digest).rstrip(b"=").decode()
    payload = urlsafe_b64decode(started.cookie.split(".")[0] + "==")
    assert verifier.encode() not in payload

    sent: dict[str, str] = {}

    def record(url: str, data: dict[str, str], what: str) -> TokenReply:
        sent.update(data)
        return TokenReply(id_token="x")

    monkeypatch.setattr(google_module, "token_request", record)
    Google().exchange("code", "http://x/cb", verifier)
    assert sent["code_verifier"] == verifier
    sent.clear()
    Google().exchange("code", "http://x/cb", "")
    assert "code_verifier" not in sent


def _callback(started, state: str) -> flow.Callback:
    return flow.Callback(
        code="c",
        state=state,
        error="",
        cookie=started.cookie,
        site="http://x/",
        signed_in=False,
    )


def _started_google() -> tuple[consent.ConsentStart, str]:
    started = consent.begin(Google(), "http://x/", "/", None)
    return started, parse_qs(urlsplit(started.url).query)["state"][0]


def _no_access(self, reply, nonce):
    msg = "no"
    raise SignInError(msg)


@pytest.mark.usefixtures("google_on")
def test_the_verifier_is_used_by_one_callback_only(monkeypatch):
    started, state = _started_google()
    exchanged: list[str] = []

    def record(self, code: str, redirect_uri: str, verifier: str) -> TokenReply:
        exchanged.append(verifier)
        return TokenReply(id_token=_token())

    monkeypatch.setattr(Google, "exchange", record)
    monkeypatch.setattr(Google, "identity", _no_access)

    flow.finish(Google(), _callback(started, state))
    again = flow.finish(Google(), _callback(started, state))

    assert len(exchanged) == 1
    assert again.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.usefixtures("google_on")
def test_a_down_store_fails_the_callback_without_an_exchange(monkeypatch):
    started, state = _started_google()
    exchanged: list[str] = []

    def record(self, *args) -> TokenReply:
        exchanged.append("x")
        return TokenReply(id_token=_token())

    monkeypatch.setattr(Google, "exchange", record)

    class Down:
        def getdel(self, _key):
            msg = "down"
            raise RedisError(msg)

    monkeypatch.setattr(signin_state, "get_redis", Down)

    outcome = flow.finish(Google(), _callback(started, state))

    assert exchanged == []
    assert outcome.status_code == HTTPStatus.BAD_REQUEST
    assert outcome.error == "Sign-in did not work. Try again."


@pytest.mark.usefixtures("google_on")
def test_a_down_store_refuses_to_start_a_sign_in(monkeypatch):
    class Down:
        def set(self, *args, **kwargs):
            msg = "down"
            raise RedisError(msg)

    monkeypatch.setattr(signin_state, "get_redis", Down)

    with pytest.raises(UnavailableError):
        consent.begin(Google(), "http://x/", "/", None)


@pytest.mark.usefixtures("google_on")
def test_a_sign_in_started_before_the_deploy_is_refused_once():
    started, state = _started_google()
    get_redis().delete(f"signin:verifier:{state}")

    outcome = flow.finish(Google(), _callback(started, state))

    assert outcome.status_code == HTTPStatus.BAD_REQUEST
    assert outcome.error == "This sign-in took too long or was already used: try again"


def test_a_va_is_out_at_the_next_click_when_the_employer_is_deactivated():
    client = TestClient(app)
    employer = make_user("boss-live@example.com")
    va = users.add_user(
        UserForm(email="va-live@example.com", role=Role.VA, owner_id=employer.id)
    )
    assert va is not None
    client.cookies.set(SESSION_COOKIE, session_cookie(va))
    assert client.get("/generate", follow_redirects=False).status_code == 200

    users.set_active(employer.id, active=False)

    assert client.get("/generate", follow_redirects=False).status_code == 303


@pytest.mark.usefixtures("fanvue_on")
def test_a_fanvue_connect_with_redis_down_goes_back_to_the_page(monkeypatch):
    class Down:
        def set(self, *_args, **_kwargs):
            msg = "down"
            raise RedisError(msg)

    client = TestClient(app)
    admin = make_user("fv-down@example.com", Role.ADMIN)
    client.cookies.set(SESSION_COOKIE, session_cookie(admin))
    monkeypatch.setattr(fanvue_connect, "get_redis", Down)

    answer = client.get("/chatting/fanvue/connect", follow_redirects=False)

    assert answer.status_code == HTTPStatus.SEE_OTHER
    assert answer.headers["location"] == "/chatting/fanvue?note=failed"


def test_a_va_signs_in_only_while_the_employer_is_active():
    employer = make_user("boss-active@example.com")
    va = users.add_user(
        UserForm(email="va-active@example.com", role=Role.VA, owner_id=employer.id)
    )
    assert va is not None
    credentials.set_va_password(va.id, employer.id, PASSWORD)
    assert credentials.va_sign_in(employer.email, PASSWORD) is not None

    users.set_active(employer.id, active=False)

    assert credentials.va_sign_in(employer.email, PASSWORD) is None


def test_a_password_link_works_once_even_when_two_submit_at_once():
    user = make_user("link-race@example.com")
    token = credentials.password_link(user.id)
    gate = Barrier(2)

    def submit(password: str):
        gate.wait()
        return credentials.set_password_with_link(token, password, password)

    passwords = ["first long password", "other long password"]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, passwords))

    assert sorted(r is None for r in results) == [False, True]
    winner = passwords[[r is not None for r in results].index(True)]
    loser = passwords[[r is None for r in results].index(True)]
    stored = users.password_hash(user.id)
    assert verify_password(winner, stored)
    assert not verify_password(loser, stored)
    assert credentials.link_owner(token) is None


def test_a_rejected_password_keeps_the_link_for_another_try():
    user = make_user("link-retry@example.com")
    token = credentials.password_link(user.id)

    with pytest.raises(credentials.PasswordRejectedError):
        credentials.set_password_with_link(token, "a long password", "different one")

    assert credentials.link_owner(token) is not None


def test_a_password_changed_behind_the_cache_ends_the_session_at_once():
    client = TestClient(app)
    user = make_user_with_password("revoked@example.com", PASSWORD)
    sign_in(client, user.email)
    assert client.get("/generate", follow_redirects=False).status_code == 200
    assert users.get_user(user.id) is not None

    with session_scope() as s:
        row = s.get(UserRow, user.id)
        row.password_hash = "changed-behind-the-cache"

    assert signed_in_user(user.id).standing.credential_stamp != user.credential_stamp
    assert client.get("/generate", follow_redirects=False).status_code == 303


@pytest.fixture
def telegram_on(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "")


def _start_token(user_id: int) -> str:
    url = telegram.connect_url(user_id)
    return parse_qs(urlsplit(url).query)["start"][0]


def test_a_start_token_from_before_logout_links_nothing(telegram_on):
    user = make_user("tg-logout@example.com")
    token = _start_token(user.id)
    time.sleep(0.05)

    signed_out(user.id)

    assert telegram.link(Start(token=token, chat_id="555")) is False
    assert telegram.linked(user.id) is False


def test_a_start_token_from_before_a_password_change_links_nothing(telegram_on):
    user = make_user_with_password("tg-pass@example.com", PASSWORD)
    token = _start_token(user.id)

    credentials.change_password(
        user.id, PASSWORD, "another long password", "another long password"
    )

    assert telegram.link(Start(token=token, chat_id="555")) is False
    assert telegram.linked(user.id) is False


def test_a_start_token_of_the_signed_in_person_still_links(telegram_on):
    user = make_user("tg-ok@example.com")
    token = _start_token(user.id)

    assert telegram.link(Start(token=token, chat_id="556")) is True
    assert telegram.linked(user.id) is True
    assert get_redis().get(f"telegram:link:{token}") is None


def test_a_start_token_from_before_the_deploy_is_refused_with_the_plain_message(
    telegram_on,
):
    user = make_user("tg-bare@example.com")
    get_redis().set("telegram:link:bare-id-token", str(user.id), ex=60)

    first = telegram.link(Start(token="bare-id-token", chat_id="557"))
    second = telegram.link(Start(token="bare-id-token", chat_id="557"))

    assert (first, second) == (False, False)
    assert telegram.linked(user.id) is False
    titles = [m.text.splitlines()[0] for m in fake_telegram().sent]
    assert titles == ["This link is used or too old"] * 2


@pytest.fixture
def fanvue_on(fake_externals, monkeypatch):
    monkeypatch.setenv("FANVUE_CLIENT_ID", "fake-client")
    monkeypatch.setenv("FANVUE_CLIENT_SECRET", "fake-secret")


def _fanvue_state(user_id: int, session_id: str) -> str:
    url = fanvue_connect.connect_url(user_id, session_id)
    return parse_qs(urlsplit(url).query)["state"][0]


@pytest.mark.usefixtures("fanvue_on")
def test_a_fanvue_connect_state_is_only_good_for_the_admin_who_started_it():
    state = _fanvue_state(11, "tab-a")

    assert fanvue_connect.finish(12, "tab-a", fanvue_fake.GOOD_CODE, state) is False

    state = _fanvue_state(11, "tab-a")
    assert fanvue_connect.finish(11, "tab-a", fanvue_fake.GOOD_CODE, state) is True
    assert fanvue_connect.finish(11, "tab-a", fanvue_fake.GOOD_CODE, state) is False


@pytest.mark.usefixtures("fanvue_on")
def test_a_fanvue_connect_state_is_only_good_in_the_session_that_started_it():
    state = _fanvue_state(11, "session-a")

    assert fanvue_connect.finish(11, "session-b", fanvue_fake.GOOD_CODE, state) is False


@pytest.mark.usefixtures("fanvue_on")
def test_two_callbacks_with_one_fanvue_state_exchange_once(monkeypatch):
    state = _fanvue_state(11, "session-a")
    exchanges: list[str] = []
    real = fanvue_connect.oauth.exchange

    def counted(code, redirect_uri, verifier):
        exchanges.append(code)
        return real(code, redirect_uri, verifier)

    monkeypatch.setattr(fanvue_connect.oauth, "exchange", counted)
    gate = Barrier(2)

    def callback(_):
        gate.wait()
        return fanvue_connect.finish(11, "session-a", fanvue_fake.GOOD_CODE, state)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(callback, range(2)))

    assert sorted(results) == [False, True]
    assert len(exchanges) == 1
