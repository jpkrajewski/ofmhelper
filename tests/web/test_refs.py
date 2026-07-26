"""
Covers /refs/thumb: the cached small-preview endpoint the reuse-picker grid
and job-status "Inputs" section use instead of downloading full-res
originals (see task_helpers.reference_asset, static/js/file-picker.js).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.web.main import app
from ofmhelpers.web.routers import refs as refs_router


def _png_bytes(size=(300, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color="blue").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    d = tmp_path / "assets"
    d.mkdir()
    monkeypatch.setattr(refs_router, "ASSETS_ROOT", d)
    monkeypatch.setattr(refs_router, "THUMBS_DIR", d / ".thumbs")
    return d


def test_thumb_is_smaller_than_the_original(client, assets_dir):
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r = client.get(f"/refs/thumb?path={original}&size=100")

    assert r.status_code == 200
    assert len(r.content) < original.stat().st_size


def test_thumb_is_cached_on_disk_and_reused(client, assets_dir):
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r1 = client.get(f"/refs/thumb?path={original}&size=100")
    cached = list((assets_dir / ".thumbs").glob("*"))
    assert len(cached) == 1

    r2 = client.get(f"/refs/thumb?path={original}&size=100")
    assert r2.content == r1.content


def test_thumb_rejects_path_outside_assets_root(client, tmp_path, assets_dir):
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())

    r = client.get(f"/refs/thumb?path={outside}")
    assert r.status_code == 403


def test_thumb_404s_for_non_image_kind(client, assets_dir):
    video = assets_dir / "abc123__clip.mp4"
    video.write_bytes(b"not a real video, doesn't matter here")

    r = client.get(f"/refs/thumb?path={video}")
    assert r.status_code == 404


def test_thumb_404s_for_missing_file(client, assets_dir):
    missing = assets_dir / "abc123__gone.png"
    r = client.get(f"/refs/thumb?path={missing}")
    assert r.status_code == 404


def test_list_refs_still_returns_full_res_file_path(client, assets_dir):
    """/refs itself is unchanged -- the client still gets the real path, and
    still uses /refs/file for the actual (full-res) selected asset."""
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r = client.get("/refs?kind=image")
    assert r.status_code == 200
    [entry] = r.json()
    assert entry["path"] == str(original)

    file_r = client.get(f"/refs/file?path={entry['path']}")
    assert file_r.content == original.read_bytes()
