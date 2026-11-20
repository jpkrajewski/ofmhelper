from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from notifications_socket import first_sent
from signin import make_user, sign_in, sign_in_admin

from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.schema import DeviceFields
from ofmhelpers.db.users import store as user_store
from ofmhelpers.db.users.schema import Role
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.features.notifications.schema import Notification
from ofmhelpers.web.main import app

BOSS = "boss@example.com"
MANAGER = "manager@example.com"
PAGE = {"sec-fetch-mode": "navigate"}


@pytest.fixture
def boss() -> int:
    return make_user(BOSS, Role.ADMIN).id


@pytest.fixture
def manager(boss) -> TestClient:
    found = make_user(MANAGER, Role.MANAGER)
    user_store.set_owner(found.id, boss)
    client = TestClient(app)
    sign_in(client, MANAGER, Role.MANAGER)
    return client


def test_a_manager_sees_their_employers_pages(boss, manager):
    devices.add_device(DeviceFields(label="Boss phone 7"), boss)

    page = manager.get("/phones", headers=PAGE)

    assert page.status_code == 200
    assert "Boss phone 7" in page.text
    assert MANAGER in page.text
    assert "Viewing as" not in page.text
    for path in ("/vas", "/chatting", "/drive", "/accounts", "/generate", "/clone"):
        assert manager.get(path, headers=PAGE).status_code == 200, path


def test_a_manager_sees_no_password_and_runs_nothing(boss, manager):
    phone = devices.add_device(DeviceFields(label="Boss phone 8"), boss)

    refused = manager.post("/phones/add", data={"label": "Mine"})
    run = manager.post("/ai/run", data={"prompt": "x"})
    hide = manager.post(f"/phones/{phone}/retired", data={"retired": "true"})

    for answer in (refused, run, hide):
        assert answer.status_code == 403
        assert "A manager looks only" in answer.text
    assert [d.label for d in devices.list_devices(boss)] == ["Boss phone 8"]


@pytest.mark.parametrize("path", ["/account", "/cookies", "/users", "/action-log"])
def test_a_manager_never_opens_their_own_business_or_the_admin_tools(manager, path):
    answer = manager.get(path, headers=PAGE, follow_redirects=False)

    assert answer.status_code == 303
    assert answer.headers["location"] == "/home"


def test_a_manager_cannot_connect_a_chat_login(manager):
    page = manager.get("/chatting/fanvue", headers=PAGE)
    connect = manager.get(
        "/chatting/fanvue/connect", headers=PAGE, follow_redirects=False
    )

    assert "Connect Fanvue" not in page.text
    assert connect.status_code == 303
    assert connect.headers["location"] == "/home"


def test_the_menu_is_a_managers(manager):
    page = manager.get("/home", headers=PAGE).text

    assert 'href="/chatting"' in page
    assert 'href="/vas"' in page
    assert 'href="/account"' not in page
    assert 'href="/users"' not in page


def test_a_manager_without_an_employer_sees_nothing_of_anyone(boss):
    devices.add_device(DeviceFields(label="Boss phone 9"), boss)
    client = TestClient(app)
    sign_in(client, "loose@example.com", Role.MANAGER)

    page = client.get("/phones", headers=PAGE)

    assert page.status_code == 200
    assert "Boss phone 9" not in page.text


def test_a_users_manager_sees_their_pages_but_no_chats():
    owner = make_user("owner@example.com").id
    devices.add_device(DeviceFields(label="Owner phone 3"), owner)
    found = make_user("their.manager@example.com", Role.MANAGER)
    user_store.set_owner(found.id, owner)
    client = TestClient(app)
    sign_in(client, "their.manager@example.com", Role.MANAGER)

    phones = client.get("/phones", headers=PAGE)
    chats = client.get("/chatting", headers=PAGE, follow_redirects=False)
    home = client.get("/home", headers=PAGE).text

    assert "Owner phone 3" in phones.text
    assert chats.status_code == 303
    assert 'href="/chatting"' not in home
    assert 'href="/vas"' in home
    assert client.get("/vas", headers=PAGE).status_code == 200


FETCH = {"sec-fetch-mode": "cors"}


CHAT_LINE = Notification(
    id="reconnect:fanvue:1",
    event_ids=["reconnect:fanvue:1"],
    title="@agency",
    text="Connect Fanvue again",
    url="/chatting/fanvue",
    at=datetime.now(UTC),
)


@pytest.fixture
def chat_line(monkeypatch) -> None:
    monkeypatch.setattr(notifications, "_reconnects", lambda _now: [CHAT_LINE])


def _chats_on_notifications(client: TestClient) -> bool:
    return CHAT_LINE.id in [n["id"] for n in first_sent(client)]


def test_a_manager_whose_employer_was_deactivated_gets_no_chats(chat_line):
    owner = make_user("leaving.owner@example.com").id
    found = make_user("left.manager@example.com", Role.MANAGER)
    user_store.set_owner(found.id, owner)
    client = TestClient(app)
    sign_in(client, "left.manager@example.com", Role.MANAGER)
    assert not _chats_on_notifications(client)

    user_store.set_active(owner, active=False)

    assert not _chats_on_notifications(client)
    assert client.get("/chatting", headers=FETCH).status_code == 403
    home = client.get("/home", headers=PAGE)
    assert home.status_code == 200
    assert 'href="/chatting"' not in home.text


def test_an_admins_manager_keeps_the_chats_after_the_fix(manager, chat_line):
    assert _chats_on_notifications(manager)


def test_an_admins_manager_loses_the_chats_when_the_admin_is_deactivated(
    boss, manager, chat_line
):
    user_store.set_active(boss, active=False)

    assert not _chats_on_notifications(manager)
    assert manager.get("/home", headers=PAGE).status_code == 200


def test_an_admin_adds_a_manager_for_any_user():
    client = TestClient(app)
    sign_in_admin(client)
    plain = make_user("plain@example.com").id
    va = make_user("a.va@example.com", Role.VA).id

    for_a_va = client.post(
        "/users",
        data={"email": "m1@example.com", "role": "manager", "owner_id": str(va)},
    )
    nobody = client.post("/users", data={"email": "m3@example.com", "role": "manager"})
    added = client.post(
        "/users",
        data={"email": "m2@example.com", "role": "manager", "owner_id": str(plain)},
    )

    assert for_a_va.status_code == 400
    assert user_store.by_email("m1@example.com") is None
    assert nobody.status_code == 400
    assert "employer" in nobody.text
    assert added.status_code == 200
    made = user_store.by_email("m2@example.com")
    assert made is not None
    assert (made.role, made.owner_id) == (Role.MANAGER, plain)
