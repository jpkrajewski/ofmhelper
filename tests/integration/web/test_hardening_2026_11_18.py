import time

import pytest
from fastapi.testclient import TestClient
from signin import make_user, make_user_with_password, sign_in

from ofmhelpers.cache import get_redis
from ofmhelpers.cache.rate_limit import clear_failed_user_logins
from ofmhelpers.db.models import DeviceLoginRow, LoginRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import devices, event_targets, users
from ofmhelpers.db.stores import distribution as distribution_store
from ofmhelpers.features.account import telegram
from ofmhelpers.features.assets import store
from ofmhelpers.features.events import Event
from ofmhelpers.features.events.dispatch import dispatch
from ofmhelpers.features.signin import credentials
from ofmhelpers.features.trials.state import work_dir
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.integrations.senders.telegram.inbox import Start
from ofmhelpers.integrations.signin.passwords import hash_password, verify_password
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app

PASSWORD = "correct horse battery"
NEW = "a brand new password"


def test_a_deactivated_owners_accounts_are_not_running():
    owner = make_user("pins@example.com")
    repo = distribution_store.repository()
    channel = repo.connect(owner.id, "pinterest", "pins", "cookies", None)
    assert [c.id for c in repo.running()] == [channel.id]

    users.set_active(owner.id, active=False)

    assert repo.running() == []


@pytest.fixture
def telegram_on(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "")


def test_an_inactive_persons_event_goes_nowhere(telegram_on):
    owner = make_user("gone@example.com")
    event_targets.save(owner.id, "telegram", "555")
    users.set_active(owner.id, active=False)

    event = Event(name="vas.login", title="x", owner_id=owner.id)
    dispatch(event.model_dump(mode="json"))

    assert fake_telegram().sent == []


def test_an_inactive_persons_start_token_links_nothing(telegram_on):
    owner = make_user("gone2@example.com")
    get_redis().set("telegram:link:tok-inactive", str(owner.id))
    users.set_active(owner.id, active=False)

    linked = telegram.link(Start(token="tok-inactive", chat_id="555"))

    assert linked is False
    assert event_targets.of(owner.id, "telegram") is None


def test_unlinking_ends_the_other_sessions_and_keeps_this_one(monkeypatch):
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("OFM_GOOGLE_CLIENT_SECRET", "google-secret")
    me = make_user_with_password("unlink@example.com", PASSWORD)
    assert users.link_identity(me.id, _google("sub-unlink"))
    mine, other = TestClient(app), TestClient(app)
    sign_in(mine, me.email)
    sign_in(other, me.email)
    time.sleep(0.05)

    answer = mine.post("/account/unlink/google", follow_redirects=False)

    assert answer.status_code == 303
    assert mine.get("/account", follow_redirects=False).status_code == 200
    assert other.get("/account", follow_redirects=False).status_code == 303


def _google(subject: str):
    from ofmhelpers.integrations.signin import Identity

    return Identity(provider="google", subject=subject, email="u@gmail.com")


def test_wrong_current_passwords_are_braked_per_user(monkeypatch):
    monkeypatch.setenv("OFM_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("OFM_LOGIN_MAX_FAILURES", "2")
    me = make_user_with_password("brake@example.com", PASSWORD)
    client = TestClient(app)
    sign_in(client, me.email)
    form = {"password": NEW, "confirm": NEW}
    try:
        for _ in range(2):
            wrong = client.post("/account/password", data={"current": "nope", **form})
            assert "current password is wrong" in wrong.text
        braked = client.post("/account/password", data={"current": PASSWORD, **form})
        assert "Too many failed attempts" in braked.text
        assert verify_password(PASSWORD, users.password_hash(me.id))
    finally:
        clear_failed_user_logins(me.id)


def test_the_password_is_written_only_while_it_is_the_one_verified(monkeypatch):
    me = make_user_with_password("cas@example.com", PASSWORD)
    real = credentials.verify_password

    def changed_meanwhile(password: str, stored: str) -> bool:
        ok = real(password, stored)
        users.set_password_hash(me.id, hash_password("someone else won"))
        return ok

    monkeypatch.setattr(credentials, "verify_password", changed_meanwhile)
    with pytest.raises(credentials.PasswordRejectedError, match="changed meanwhile"):
        credentials.change_password(me.id, PASSWORD, NEW, NEW)

    assert real("someone else won", users.password_hash(me.id))


def test_the_agency_root_excludes_the_thumbnail_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    root = store.assets_root()
    (root / ".thumbs").mkdir(parents=True)
    (root / ".thumbs" / "a.webp").write_bytes(b"x")
    (root / "b.png").write_bytes(b"x")

    assert not store.in_asset_root(root, root / ".thumbs" / "a.webp")
    assert store.in_asset_root(root, root / "b.png")


def test_a_login_that_is_not_theirs_is_a_404_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    phone = devices.add_device(
        DeviceFields(label="Phone 07", model_id="iphone-13", color_id="blue", owner="A")
    )
    added = devices.add_login(phone, LoginFields(service="instagram", username="x.ig"))
    assert added is not None
    login = added.id
    stranger = TestClient(app)
    user = sign_in(stranger, "stranger@example.com")

    answers = [
        stranger.post(f"/trials/{login}/generate"),
        stranger.post(f"/trials/{login}/upload"),
    ]

    assert [r.status_code for r in answers] == [404, 404]
    assert not (work_dir(user.id, login) / "state.json").exists()


def _orphan(owner_id: int, username: str) -> int:
    with session_scope() as s:
        row = LoginRow(
            service="instagram", provider="", username=username, user_id=owner_id
        )
        s.add(row)
        s.flush()
        return row.id


def test_deleting_a_phone_drops_only_its_owners_orphan_logins():
    mine, theirs = make_user("o1@example.com"), make_user("o2@example.com")
    phone = devices.add_device(
        DeviceFields(label="P1", model_id="iphone-13", color_id="blue", owner="A"),
        mine.id,
    )
    mine_orphan = _orphan(mine.id, "mine.orphan")
    their_orphan = _orphan(theirs.id, "their.orphan")

    assert devices.delete_device(phone, mine.id)

    with session_scope() as s:
        assert s.get(LoginRow, mine_orphan) is None
        assert s.get(LoginRow, their_orphan) is not None
        assert s.query(DeviceLoginRow).filter_by(login_id=their_orphan).count() == 0
