"""A VA never sees what an admin did: jobs (galleries, status pages, files)
and the reuse picker's asset store. An admin still sees everything."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import USER_EMAIL, sign_in

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.stores import users


def _login(password: str) -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"password": password, "next": "/"})
    return c


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="red").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def admin():
    return _login("test-admin")


@pytest.fixture
def va():
    c = TestClient(app)
    sign_in(c)
    return c


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path)
    return tmp_path


def _fake_ai_job(client: TestClient, prompt: str) -> str:
    """A fake-provider job with one uploaded reference. Its outcome is an
    error, so the only file it adds to the asset store is that reference."""
    r = client.post(
        "/ai/run",
        data={
            "provider": "fake",
            "model": "fake:image",
            "prompt": prompt,
            "delay": "0",
            "outcome": "error",
            "reference_image_urls_manifest": '[{"kind": "new"}]',
        },
        files={"reference_image_urls": ("ref.png", _png_bytes(), "image/png")},
    )
    return r.json()["job_id"]


def test_va_cannot_reach_an_admin_job(admin, va, assets_dir):
    job_id = _fake_ai_job(admin, "admin secret")

    assert va.get(f"/ai/jobs/{job_id}").status_code == 404
    assert va.get(f"/ai/jobs/{job_id}/status").status_code == 404
    assert va.get(f"/ai/files/{job_id}/0").status_code == 404
    assert "admin secret" not in va.get("/generate").text


def test_admin_still_sees_a_va_job(admin, va, assets_dir):
    job_id = _fake_ai_job(va, "va work")

    assert admin.get(f"/ai/jobs/{job_id}/status").status_code == 200
    assert "va work" in admin.get("/generate").text
    assert "va work" in va.get("/generate").text


def test_va_reuse_picker_excludes_admin_uploads(admin, va, assets_dir):
    _fake_ai_job(admin, "a")
    [admin_file] = admin.get("/refs?kind=image").json()

    assert va.get("/refs?kind=image").json() == []
    assert va.get(f"/refs/file?path={admin_file['path']}").status_code == 403
    assert va.get(f"/refs/thumb?path={admin_file['path']}").status_code == 403


def test_va_uploads_land_in_the_va_folder_and_admin_sees_them(admin, va, assets_dir):
    _fake_ai_job(va, "v")

    [stored] = (assets_dir / "users" / str(users.by_email(USER_EMAIL).id)).iterdir()
    assert [e["path"] for e in va.get("/refs?kind=image").json()] == [str(stored)]
    assert str(stored) in [e["path"] for e in admin.get("/refs?kind=image").json()]


def test_va_cannot_reuse_an_admin_file_by_path(admin, va, assets_dir):
    _fake_ai_job(admin, "a")
    [admin_file] = admin.get("/refs?kind=image").json()

    r = va.post(
        "/ai/run",
        data={
            "provider": "fake",
            "model": "fake:image",
            "prompt": "p",
            "reference_image_urls_manifest": json.dumps(
                [{"kind": "existing", "path": admin_file["path"]}]
            ),
        },
    )
    assert r.status_code == 400
