"""A manager (owner 2026-11-15: "a new user type called Manager: cannot
generate anything, sees everything a normal user can see, plus chatting and
/vas spending"): their employer's (an admin's) pages, looked at, nothing
changed or run; the chats; never /account, /cookies or the admin tools."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in, sign_in_admin

from ofmhelpers.db.stores import devices
from ofmhelpers.db.stores import users as user_store
from ofmhelpers.schemas import DeviceFields, Role
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
    assert MANAGER in page.text  # signed in as themselves
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
    """Any user may have a manager (owner 2026-11-16); the chats are an
    admin's, so a plain user's manager has none."""
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

    assert for_a_va.status_code == 400  # a VA employs nobody
    assert user_store.by_email("m1@example.com") is None
    assert nobody.status_code == 400
    assert "employer" in nobody.text
    assert added.status_code == 200
    made = user_store.by_email("m2@example.com")
    assert made is not None
    assert (made.role, made.owner_id) == (Role.MANAGER, plain)
