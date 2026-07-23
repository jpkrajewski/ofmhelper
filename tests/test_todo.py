"""
Covers the admin-managed Todo page (web/todos.py + routers/todo.py): admins
add "model name / link to replicate / comments" tasks for VAs to work
through. VAs can see the list but every write endpoint
(add/toggle/delete/export/import) is enforced admin-only server-side, not
just hidden in the template -- a VA could otherwise still POST straight to
these routes.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web import todos
from ofmhelpers.web.jobs import get_job, JOBS
from ofmhelpers.web.routers import todo as todo_router


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
    # Same for asset uploads -- keep them out of the real uploads/ dir.
    monkeypatch.setattr(todo_router, "ASSET_ROOT", tmp_path / "todo_assets")


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


def test_admin_can_export_todos_as_json(client):
    todos.add_todo("Model A", "https://a", "note", "admin")

    r = client.get("/todo/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="todos.json"' in r.headers["content-disposition"]

    exported = r.json()
    assert len(exported) == 1
    assert exported[0]["model_name"] == "Model A"


def test_va_cannot_export(client, va_client):
    todos.add_todo("Model A", "https://a", "", "admin")
    assert va_client.get("/todo/export").status_code == 403


def test_admin_can_import_todos_json(client):
    payload = [
        {"model_name": "Model A", "url": "https://a", "comments": "first"},
        {"model_name": "Model B", "url": "https://b", "comments": ""},
    ]
    files = {"file": ("todos.json", json.dumps(payload), "application/json")}
    r = client.post("/todo/import", files=files, follow_redirects=False)
    assert r.status_code == 303

    items = todos.list_todos()
    assert len(items) == 2
    names = {t["model_name"] for t in items}
    assert names == {"Model A", "Model B"}
    assert all(t["created_by"] == "admin" for t in items)
    assert all(t["checked"] is False for t in items)


def test_va_cannot_import(client, va_client):
    payload = [{"model_name": "Model A", "url": "https://a", "comments": ""}]
    files = {"file": ("todos.json", json.dumps(payload), "application/json")}
    r = va_client.post("/todo/import", files=files)
    assert r.status_code == 403
    assert todos.list_todos() == []


def test_import_rejects_invalid_json(client):
    files = {"file": ("todos.json", "not json at all", "application/json")}
    r = client.post("/todo/import", files=files)
    assert r.status_code == 400
    assert todos.list_todos() == []


def test_import_rejects_non_list_json(client):
    files = {
        "file": (
            "todos.json",
            json.dumps({"model_name": "Model A", "url": "https://a"}),
            "application/json",
        )
    }
    r = client.post("/todo/import", files=files)
    assert r.status_code == 400
    assert todos.list_todos() == []


def test_import_is_all_or_nothing_on_bad_entry(client):
    payload = [
        {"model_name": "Model A", "url": "https://a", "comments": ""},
        {"model_name": "Model B", "url": "   ", "comments": ""},  # blank url
    ]
    files = {"file": ("todos.json", json.dumps(payload), "application/json")}
    r = client.post("/todo/import", files=files)
    assert r.status_code == 400
    assert todos.list_todos() == [], "a bad row must not let earlier rows through"


def test_import_ignores_uploaded_id_checked_and_created_by(client):
    payload = [
        {
            "id": "forged00",
            "model_name": "Model A",
            "url": "https://a",
            "comments": "",
            "checked": True,
            "created_by": "someone-else",
        }
    ]
    files = {"file": ("todos.json", json.dumps(payload), "application/json")}
    client.post("/todo/import", files=files)

    items = todos.list_todos()
    assert len(items) == 1
    assert items[0]["id"] != "forged00"
    assert items[0]["checked"] is False
    assert items[0]["created_by"] == "admin"


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


def test_va_can_attach_asset_and_it_shows_up_with_preview(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")

    files = {"file": ("ready.png", b"fake image bytes", "image/png")}
    r = va_client.post(f"/todo/{todo['id']}/asset", files=files, follow_redirects=False)
    assert r.status_code == 303

    stored = todos.get_todo(todo["id"])
    assert stored["asset_name"] == "ready.png"
    assert stored["approved"] is False
    assert stored["drive_file_id"] is None

    html = client.get("/todo").text
    assert f"/todo/{todo['id']}/asset" in html


def test_asset_upload_404s_for_unknown_todo(va_client):
    files = {"file": ("ready.png", b"bytes", "image/png")}
    r = va_client.post("/todo/doesnotexist/asset", files=files)
    assert r.status_code == 404


def test_view_asset_serves_the_uploaded_file(va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"fake image bytes", "image/png")}
    va_client.post(f"/todo/{todo['id']}/asset", files=files)

    r = va_client.get(f"/todo/{todo['id']}/asset")
    assert r.status_code == 200
    assert r.content == b"fake image bytes"


def test_view_asset_404s_when_none_attached(va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    assert va_client.get(f"/todo/{todo['id']}/asset").status_code == 404


def test_admin_can_approve_asset_va_cannot(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)

    r = va_client.post(f"/todo/{todo['id']}/approve")
    assert r.status_code == 403
    assert todos.get_todo(todo["id"])["approved"] is False

    r = client.post(f"/todo/{todo['id']}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert todos.get_todo(todo["id"])["approved"] is True


def test_approve_requires_an_asset_to_already_be_attached(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    r = client.post(f"/todo/{todo['id']}/approve")
    assert r.status_code == 404
    assert todos.get_todo(todo["id"])["approved"] is False


def test_approve_404s_for_a_todo_id_that_does_not_exist_at_all(client):
    assert client.post("/todo/doesnotexist/approve").status_code == 404


def test_upload_drive_404s_for_a_todo_id_that_does_not_exist_at_all(client):
    assert client.post("/todo/doesnotexist/upload-drive").status_code == 404


def test_admin_upload_to_drive_requires_approval_first(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)

    r = client.post(f"/todo/{todo['id']}/upload-drive")
    assert r.status_code == 400
    assert todos.get_todo(todo["id"])["drive_file_id"] is None


def test_admin_upload_to_drive_calls_gdrive_client_and_records_file_id(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    with mock.patch.object(
        todo_router, "gdrive_upload_file", return_value="drive-file-123"
    ) as upload_mock:
        r = client.post(f"/todo/{todo['id']}/upload-drive", follow_redirects=False)

    assert r.status_code == 303
    upload_mock.assert_called_once()
    stored = todos.get_todo(todo["id"])
    assert stored["drive_file_id"] == "drive-file-123"
    assert stored["drive_uploaded_at"] is not None


def test_va_cannot_upload_to_drive(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    r = va_client.post(f"/todo/{todo['id']}/upload-drive")
    assert r.status_code == 403
    assert todos.get_todo(todo["id"])["drive_file_id"] is None


def test_new_asset_upload_resets_prior_approval_and_upload_state(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    with mock.patch.object(todo_router, "gdrive_upload_file", return_value="id-1"):
        client.post(f"/todo/{todo['id']}/upload-drive")
    assert todos.get_todo(todo["id"])["drive_file_id"] == "id-1"

    files2 = {"file": ("v2.png", b"new bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files2)

    stored = todos.get_todo(todo["id"])
    assert stored["asset_name"] == "v2.png"
    assert stored["approved"] is False
    assert stored["drive_file_id"] is None


def test_upload_to_drive_runs_as_a_background_job_not_inline(client):
    """The whole point of backgrounding: the route hands off to
    BackgroundTasks/run_job (see web/jobs.py) instead of calling the Drive
    client directly in the request, so a slow/large upload can't tie up the
    request indefinitely. Verified here by checking a job record was
    created and wired onto the todo, not by timing the request."""
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    with mock.patch.object(todo_router, "gdrive_upload_file", return_value="id-1"):
        client.post(f"/todo/{todo['id']}/upload-drive")

    job_id = todos.get_todo(todo["id"])["drive_upload_job_id"]
    assert job_id is not None
    job = get_job(job_id)
    assert job["task"] == "todo_drive_upload"
    assert job["status"] == "done"


def test_upload_to_drive_failure_is_recorded_on_the_job_not_raised(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    with mock.patch.object(
        todo_router, "gdrive_upload_file", side_effect=RuntimeError("quota exceeded")
    ):
        r = client.post(f"/todo/{todo['id']}/upload-drive", follow_redirects=False)

    assert (
        r.status_code == 303
    ), "the request itself succeeds -- the upload failed, not the click"
    stored = todos.get_todo(todo["id"])
    assert stored["drive_file_id"] is None
    job = get_job(stored["drive_upload_job_id"])
    assert job["status"] == "failed"
    assert job["error"] == "quota exceeded"


def test_upload_to_drive_does_not_start_a_second_job_while_one_is_running(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    # Simulate an in-flight job without actually blocking -- TestClient runs
    # background tasks synchronously, so there's no other way to observe a
    # "still running" job mid-request.
    JOBS["fake-running-job"] = {
        "id": "fake-running-job",
        "task": "todo_drive_upload",
        "status": "running",
        "result": None,
        "error": None,
    }
    todos.set_drive_upload_job(todo["id"], "fake-running-job")

    with mock.patch.object(todo_router, "gdrive_upload_file") as upload_mock:
        client.post(f"/todo/{todo['id']}/upload-drive")

    upload_mock.assert_not_called()
    assert todos.get_todo(todo["id"])["drive_upload_job_id"] == "fake-running-job"


def test_mark_uploaded_ignores_a_result_for_a_since_replaced_asset():
    """Guards the race in todos.mark_uploaded: if a VA replaces the asset
    while an old upload is still in flight, the old job's eventual result
    must not be applied to the new (unapproved, not-yet-uploaded) asset."""
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    todos.attach_asset(todo["id"], "/old/path.png", "old.png")

    todos.attach_asset(todo["id"], "/new/path.png", "new.png")

    applied = todos.mark_uploaded(todo["id"], "/old/path.png", "stale-drive-id")

    assert applied is False
    stored = todos.get_todo(todo["id"])
    assert stored["drive_file_id"] is None
    assert stored["asset_path"] == "/new/path.png"


def test_asset_cell_fragment_reflects_running_job(client):
    """Covers the fragment endpoint the page's JS fetches after every asset
    action, and every 2s for a row that's mid-upload -- it must return just
    the Asset cell's contents (no <html>/nav chrome), not a full page, since
    the JS swaps it straight into that one <td>'s innerHTML."""
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    JOBS["fake-running-job"] = {
        "id": "fake-running-job",
        "task": "todo_drive_upload",
        "status": "running",
        "result": None,
        "error": None,
    }
    todos.set_drive_upload_job(todo["id"], "fake-running-job")

    r = client.get(f"/todo/{todo['id']}/asset-cell")
    assert r.status_code == 200
    assert "todo-uploading" in r.text
    assert "ready.png" in r.text
    assert "<html" not in r.text
    assert "<nav" not in r.text


def test_asset_cell_fragment_reflects_completed_upload(client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    with mock.patch.object(todo_router, "gdrive_upload_file", return_value="id-1"):
        client.post(f"/todo/{todo['id']}/upload-drive")

    r = client.get(f"/todo/{todo['id']}/asset-cell")
    assert r.status_code == 200
    assert "todo-drive-uploaded" in r.text
    assert "todo-uploading" not in r.text


def test_asset_cell_404s_for_unknown_todo(client):
    assert client.get("/todo/doesnotexist/asset-cell").status_code == 404


def test_asset_cell_is_viewable_by_va_and_hides_admin_action_buttons(client, va_client):
    """The write endpoints are already proven admin-only via 403s above --
    this covers the other half: a VA must not even *see* the approve/
    upload-to-drive controls (matches this module's own stated philosophy of
    not just hiding admin actions in the template, applied in reverse -- the
    fragment a VA's browser polls must agree with what the full page shows
    them)."""
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)

    # Unapproved: VA sees a status message, not the Approve button/form.
    va_html = va_client.get(f"/todo/{todo['id']}/asset-cell").text
    assert va_client.get(f"/todo/{todo['id']}/asset-cell").status_code == 200
    assert "Awaiting admin approval" in va_html
    assert f'action="/todo/{todo["id"]}/approve"' not in va_html

    admin_html = client.get(f"/todo/{todo['id']}/asset-cell").text
    assert f'action="/todo/{todo["id"]}/approve"' in admin_html

    # Approved: VA sees a status message, not the Upload-to-Drive form.
    client.post(f"/todo/{todo['id']}/approve")
    va_html = va_client.get(f"/todo/{todo['id']}/asset-cell").text
    assert "Approved" in va_html
    assert f'action="/todo/{todo["id"]}/upload-drive"' not in va_html

    admin_html = client.get(f"/todo/{todo['id']}/asset-cell").text
    assert f'action="/todo/{todo["id"]}/upload-drive"' in admin_html


def test_full_page_also_hides_admin_asset_action_buttons_from_va(client, va_client):
    todo = todos.add_todo("Model A", "https://a", "", "admin")
    files = {"file": ("ready.png", b"bytes", "image/png")}
    client.post(f"/todo/{todo['id']}/asset", files=files)
    client.post(f"/todo/{todo['id']}/approve")

    va_html = va_client.get("/todo").text
    assert f'action="/todo/{todo["id"]}/upload-drive"' not in va_html
    assert "Approved" in va_html

    admin_html = client.get("/todo").text
    assert f'action="/todo/{todo["id"]}/upload-drive"' in admin_html
