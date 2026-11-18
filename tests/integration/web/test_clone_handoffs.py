"""A finished clone goes on to /edit or /clone-long with its video already
picked there (`POST /clone/{job}/handoffs`, features/assets/send.py; owner
2026-11-16: "there is no option to click edit from clone")."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.web.main import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _finished_clone(client: TestClient, tmp_path: Path) -> str:
    video = tmp_path / "kie_out.mp4"
    video.write_bytes(b"a clone")
    job_id = create_job("clone", {"provider": "fake"}, actor=str(client.user.id))
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})
    return job_id


@pytest.mark.parametrize("target", ["edit", "clone-long"])
def test_a_clone_opens_on_edit_or_clone_long_with_its_video(client, tmp_path, target):
    job = _finished_clone(client, tmp_path)

    r = client.post(f"/clone/{job}/handoffs", data={"target": target})

    assert r.status_code == 200, r.text
    url = urlsplit(r.json()["redirect"])
    assert url.path == f"/{target}"
    (video,) = parse_qs(url.query)["video"]
    assert Path(video).read_bytes() == b"a clone"
    assert "send-to-edit" in client.get("/clone").text


def test_another_persons_clone_is_not_found(client, tmp_path):
    job = _finished_clone(client, tmp_path)
    other = TestClient(app)
    sign_in(other, "someone.else@example.com")

    r = other.post(f"/clone/{job}/handoffs", data={"target": "edit"})

    assert r.status_code == 404
