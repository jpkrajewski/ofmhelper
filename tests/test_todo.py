"""
Covers the admin-managed Todo page (web/todos.py + routers/todo.py): admins
add "model name / link to replicate / comments" tasks for VAs to work
through. VAs can see the list but every write endpoint (add/toggle/delete)
is enforced admin-only server-side, not just hidden in the template -- a VA
could otherwise still POST straight to these routes.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web import todos


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def va_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    # Every test in this module writes todos -- point STORE_FILE at a temp
    # file so we never touch the real uploads/todos.json.
    monkeypatch.setattr(todos, "STORE_FILE", tmp_path / "todos.json")


def test_empty_page_renders_for_both_roles(client, va_client):
    r = client.get("/todo")
    assert r.status_code == 200
    assert "No tasks yet." in r.text

    r = va_client.get("/todo")
    assert r.status_code == 200
    assert "No tasks yet." in r.text


def test_admin_sees_add_form_va_does_not(client, va_client):
    admin_html = client.get("/todo").text
    assert 'name="model_name"' in admin_html
    assert 'action="/todo/add"' in admin_html

    va_html = va_client.get("/todo").text
    assert 'name="model_name"' not in va_html
    assert 'action="/todo/add"' not in va_html
    assert "only an admin can add" in va_html


def test_admin_can_add_todo_and_it_appears(client):
    r = client.post(
        "/todo/add",
        data={
            "model_name": "Seedance 2.0",
            "url": "https://replicate.com/x",
            "comments": "try this one",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    html = client.get("/todo").text
    assert "Seedance 2.0" in html
    assert "https://replicate.com/x" in html
    assert "try this one" in html

    items = todos.list_todos()
    assert len(items) == 1
    assert items[0]["created_by"] == "admin"
    assert items[0]["checked"] is False


def test_va_cannot_add_todo(client, va_client):
    r = va_client.post(
        "/todo/add",
        data={
            "model_name": "Kling 3.0",
            "url": "https://replicate.com/y",
            "comments": "",
        },
    )
    assert r.status_code == 403
    assert todos.list_todos() == []


def test_add_rejects_blank_model_name_or_url(client):
    r = client.post(
        "/todo/add", data={"model_name": "   ", "url": "https://x", "comments": ""}
    )
    assert r.status_code == 400

    r = client.post("/todo/add", data={"model_name": "x", "url": "   ", "comments": ""})
    assert r.status_code == 400

    assert todos.list_todos() == []


def test_admin_sees_interactive_controls_va_sees_readonly(client, va_client):
    todos.add_todo("Model A", "https://a", "", "admin")

    admin_html = client.get("/todo").text
    assert 'todo-checkbox"' in admin_html
    assert "todo-delete-btn" in admin_html

    va_html = va_client.get("/todo").text
    assert "todo-checkbox-readonly" in va_html
    assert "todo-delete-btn" not in va_html


def test_admin_can_toggle_va_cannot(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    assert todo["checked"] is False

    r = va_client.post(f"/todo/{todo['id']}/toggle")
    assert r.status_code == 403
    assert todos.list_todos()[0]["checked"] is False

    r = client.post(f"/todo/{todo['id']}/toggle", follow_redirects=False)
    assert r.status_code == 303
    assert todos.list_todos()[0]["checked"] is True


def test_admin_can_delete_va_cannot(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")

    r = va_client.post(f"/todo/{todo['id']}/delete")
    assert r.status_code == 403
    assert len(todos.list_todos()) == 1

    r = client.post(f"/todo/{todo['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert todos.list_todos() == []


def test_toggle_and_delete_404_for_unknown_id(client):
    assert client.post("/todo/doesnotexist/toggle").status_code == 404
    assert client.post("/todo/doesnotexist/delete").status_code == 404


def test_list_todos_sorted_newest_first():
    todos._save(
        [
            {
                "id": "a",
                "model_name": "First",
                "url": "https://a",
                "comments": "",
                "checked": False,
                "created_at": 100.0,
                "created_by": "admin",
            },
            {
                "id": "b",
                "model_name": "Second",
                "url": "https://b",
                "comments": "",
                "checked": False,
                "created_at": 200.0,
                "created_by": "admin",
            },
        ]
    )

    items = todos.list_todos()
    assert [t["model_name"] for t in items] == ["Second", "First"]
