"""
/file-manager as the last resort (prod, 2026-10-22: a finished run lost its
card buttons and the file list could not tell one result from another):
newest first, images and videos with a thumbnail, opening in the browser;
download and delete as before. Admin only.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import sign_in

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.admin import file_manager
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import UserForm
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.jobs import create_job, fail_job, run_job


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    yield c
    c.close()


@pytest.fixture(autouse=True)
def root(monkeypatch, tmp_path):
    found = tmp_path / "kieai_out"
    found.mkdir()
    monkeypatch.setitem(file_manager.ROOTS, "kieai_out", found)
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return found


def _page(client) -> str:
    r = client.get("/file-manager/disk", params={"root": "kieai_out"})
    assert r.status_code == 200
    return r.text


def test_files_are_listed_newest_first(client, root):
    old, new = root / "old.png", root / "new.png"
    Image.new("RGB", (8, 8)).save(old)
    Image.new("RGB", (8, 8)).save(new)
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    page = _page(client)

    assert page.index("new.png") < page.index("old.png")
    assert "1 h ago" in page


def test_an_image_shows_a_thumbnail_and_opens_in_the_browser(client, root):
    Image.new("RGB", (900, 1600), "red").save(root / "result.png")

    page = _page(client)
    assert 'src="/file-manager/thumb?root=kieai_out&amp;path=result.png"' in page
    assert 'href="/file-manager/view?root=kieai_out&amp;path=result.png"' in page

    thumb = client.get(
        "/file-manager/thumb", params={"root": "kieai_out", "path": "result.png"}
    )
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as img:
        assert max(img.size) <= 480

    viewed = client.get(
        "/file-manager/view", params={"root": "kieai_out", "path": "result.png"}
    )
    assert viewed.status_code == 200
    assert viewed.headers["content-type"] == "image/png"
    assert "attachment" not in viewed.headers.get("content-disposition", "")


@pytest.mark.heavy
@pytest.mark.slow
def test_a_video_gets_its_first_frame_as_thumbnail(client, root):
    video = root / "clip.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x568:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    thumb = client.get(
        "/file-manager/thumb", params={"root": "kieai_out", "path": "clip.mp4"}
    )
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"


def test_a_broken_video_has_no_thumbnail_but_still_lists(client, root):
    (root / "broken.mp4").write_bytes(b"not a video")

    assert "broken.mp4" in _page(client)
    thumb = client.get(
        "/file-manager/thumb", params={"root": "kieai_out", "path": "broken.mp4"}
    )
    assert thumb.status_code == 404


def test_a_non_media_file_has_no_thumbnail_and_downloads_instead_of_running(
    client, root
):
    (root / "page.html").write_text("<script>alert(1)</script>")

    page = _page(client)
    assert "/file-manager/thumb?root=kieai_out&amp;path=page.html" not in page
    thumb = client.get(
        "/file-manager/thumb", params={"root": "kieai_out", "path": "page.html"}
    )
    assert thumb.status_code == 404
    viewed = client.get(
        "/file-manager/view", params={"root": "kieai_out", "path": "page.html"}
    )
    assert viewed.headers["content-type"] == "application/octet-stream"
    assert "attachment" in viewed.headers["content-disposition"]


def test_view_and_thumb_never_leave_the_root(client, root):
    for route in ("view", "thumb"):
        r = client.get(
            f"/file-manager/{route}",
            params={"root": "kieai_out", "path": "../secret.png"},
        )
        assert r.status_code == 400


def test_a_non_admin_sees_no_file(root):
    Image.new("RGB", (8, 8)).save(root / "result.png")
    c = TestClient(app)
    sign_in(c)
    for route in ("view", "thumb"):
        r = c.get(
            f"/file-manager/{route}",
            params={"root": "kieai_out", "path": "result.png"},
            follow_redirects=False,
        )
        assert r.status_code == 403
    c.close()


# --- by user, then section ----------------------------------------------------


@pytest.fixture
def assets(monkeypatch, tmp_path):
    found = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", found)
    return found


def _run(tmp_path, actor: int, task: str, name: str, *, status: str = "done") -> str:
    job_id = create_job(task, {"model": "wan-3"}, actor=str(actor))
    if status == "done":
        path = tmp_path / name
        Image.new("RGB", (64, 64), "blue").save(path)
        run_job(job_id, lambda: [{"name": name, "path": str(path)}], {})
    elif status == "failed":
        fail_job(job_id, "No credits")
    return job_id


def _section(client, user_id: int, section: str) -> str:
    r = client.get("/file-manager", params={"user": user_id, "section": section})
    assert r.status_code == 200
    return r.text


def test_users_are_listed_with_their_files_per_section(client, tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    _run(tmp_path, ann.id, "ai", "a.png")
    _run(tmp_path, ann.id, "clone", "c1.png")
    _run(tmp_path, ann.id, "clone", "c2.png")
    _run(tmp_path, ann.id, "clone", "x", status="failed")
    _run(tmp_path, ann.id, "download_media", "d.png")
    _run(tmp_path, ann.id, "uniqify", "u.png")

    page = client.get("/file-manager").text

    assert "ann@example.com" in page
    expected = {"aigen": 1, "clone": 2, "downloads": 1, "uploads": 0, "other": 1}
    for key, n in expected.items():
        assert f'href="/file-manager?user={ann.id}&section={key}">{n}</a>' in page


def test_a_section_shows_only_files_on_disk_newest_first(client, tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    bob = users.add_user(UserForm(email="bob@example.com"))
    first = _run(tmp_path, ann.id, "clone", "first.png")
    second = _run(tmp_path, ann.id, "clone", "second.png")
    _run(tmp_path, ann.id, "clone", "x", status="failed")
    _run(tmp_path, ann.id, "clone", "x", status="running")
    _run(tmp_path, ann.id, "ai", "not-a-clone.png")
    _run(tmp_path, bob.id, "clone", "bobs.png")

    page = _section(client, ann.id, "clone")

    assert page.count('class="fm-tile"') == 2
    assert page.index("second.png") < page.index("first.png")
    assert "No credits" not in page
    assert "not-a-clone.png" not in page
    assert "bobs.png" not in page
    assert f'src="/file-manager/runs/{second}/0/thumb"' in page
    assert (
        client.get(f"/file-manager/runs/{first}/0/thumb").headers["content-type"]
        == "image/webp"
    )
    assert (
        client.get(f"/file-manager/runs/{first}/0").headers["content-type"]
        == "image/png"
    )
    downloaded = client.get(f"/file-manager/runs/{first}/0", params={"download": 1})
    assert "attachment" in downloaded.headers["content-disposition"]


def test_a_long_clone_is_filed_under_clone(client, tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    _run(tmp_path, ann.id, "clone", "short.png")
    _run(tmp_path, ann.id, "clone_long", "long.png")

    page = client.get("/file-manager").text

    assert f'href="/file-manager?user={ann.id}&section=clone">2</a>' in page
    assert f'href="/file-manager?user={ann.id}&section=other">0</a>' in page
    assert "long.png" in _section(client, ann.id, "clone")


def test_a_done_run_whose_file_is_gone_shows_nothing(client, tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    _run(tmp_path, ann.id, "download_videos", "v.png")
    (tmp_path / "v.png").unlink()
    assert "fm-tile" not in _section(client, ann.id, "downloads")


def test_deleting_a_runs_file_goes_back_to_its_section(client, tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    job = _run(tmp_path, ann.id, "clone", "gone.png")

    r = client.post(f"/file-manager/runs/{job}/0/delete", follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == f"/file-manager?user={ann.id}&section=clone"
    assert not (tmp_path / "gone.png").exists()
    assert "gone.png" not in _section(client, ann.id, "clone")


def test_upload_into_a_users_store_then_see_download_delete(client, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(image, "PNG")

    r = client.post(
        f"/file-manager/assets/{ann.id}/upload",
        files=[("files", ("ref.png", image.getvalue(), "image/png"))],
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/file-manager?user={ann.id}&section=uploads"

    page = _section(client, ann.id, "uploads")
    assert page.count('class="fm-tile"') == 1
    assert ">ref.png</a>" in page
    (stored,) = (assets / "users" / str(ann.id)).iterdir()
    q = {"path": str(stored)}
    assert client.get(f"/file-manager/assets/{ann.id}", params=q).status_code == 200
    thumb = client.get(f"/file-manager/assets/{ann.id}/thumb", params=q)
    assert thumb.headers["content-type"] == "image/webp"

    client.post(f"/file-manager/assets/{ann.id}/delete", params=q)
    assert not stored.exists()


def test_a_users_store_never_serves_another_users_file(client, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    bob = users.add_user(UserForm(email="bob@example.com"))
    bobs = assets / "users" / str(bob.id) / "x.png"
    bobs.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(bobs)
    r = client.get(f"/file-manager/assets/{ann.id}", params={"path": str(bobs)})
    assert r.status_code == 404


def test_upload_into_a_disk_folder_never_overwrites(client, root):
    def upload():
        return client.post(
            "/file-manager/upload",
            data={"root": "kieai_out", "path": ""},
            files=[("files", ("a.txt", b"one", "text/plain"))],
            follow_redirects=False,
        )

    assert upload().status_code == 303
    assert (root / "a.txt").read_bytes() == b"one"
    assert upload().status_code == 409


def test_other_links_the_disk_browser(client, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    assert 'href="/file-manager/disk"' in _section(client, ann.id, "other")


def test_a_user_sees_no_ones_files(tmp_path, assets):
    ann = users.add_user(UserForm(email="ann@example.com"))
    job = _run(tmp_path, ann.id, "clone", "x.png")
    c = TestClient(app)
    sign_in(c)
    assert c.get("/file-manager").status_code == 403
    assert c.get(f"/file-manager/runs/{job}/0").status_code == 403
    assert c.post(f"/file-manager/assets/{ann.id}/upload").status_code == 403
    c.close()
