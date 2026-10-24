"""/download-media/send: a finished download's video(s) handed to /clone or
/clone-long, which open with them already picked. A download's files live in
the download area, not the asset store clone's picker reuses from, so the
endpoint registers each video into the store (as /reels does) and hands over
those store paths."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, in_asset_root
from ofmhelpers.web.stores.jobs import create_job, run_job


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _downloaded(tmp_path: Path, name: str) -> str:
    """A result file in the download area -- outside the asset store, exactly
    where a real download leaves it."""
    src = tmp_path / "downloads" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42" + name.encode())
    assert not in_asset_root(ASSETS_ROOT, src.resolve())
    return str(src)


def _download_job(paths: list[str], *, actor: str | None = None) -> str:
    """A grouped download_media job whose result is those files."""
    job_id = create_job("download_media", {"urls": ["https://x/1"]}, actor=actor)
    result = [
        DownloadResult(url="https://x/1", success=True, output_paths=[Path(p)])
        for p in paths
    ]
    run_job(job_id, lambda r=result: r, {})
    return job_id


def _redirect_videos(response) -> list[str]:
    redirect = response.json()["redirect"]
    return parse_qs(urlsplit(redirect).query)["video"]


def _prefill(html: str, form_id: str, attr: str) -> list[str]:
    tag = html.split(f'id="{form_id}"', 1)[1].split(">", 1)[0]
    return json.loads(tag.split(f"{attr}='", 1)[1].split("'", 1)[0])


def test_send_to_clone_registers_the_videos_and_opens_clone_with_them(client, tmp_path):
    job = _download_job(
        [_downloaded(tmp_path, "a.mp4"), _downloaded(tmp_path, "b.mp4")]
    )

    r = client.post("/download-media/send", data={"job_id": job, "target": "clone"})
    assert r.status_code == 200
    videos = _redirect_videos(r)
    # Each handed-over path is now a real file inside the session's store.
    assert len(videos) == 2
    assert all(
        in_asset_root(ASSETS_ROOT, Path(v)) and Path(v).is_file() for v in videos
    )

    html = client.get(r.json()["redirect"]).text
    assert _prefill(html, "clone-form", "data-prefill-videos") == videos


def test_send_to_clone_long_opens_with_only_the_first_video(client, tmp_path):
    job = _download_job(
        [_downloaded(tmp_path, "a.mp4"), _downloaded(tmp_path, "b.mp4")]
    )

    r = client.post(
        "/download-media/send", data={"job_id": job, "target": "clone-long"}
    )
    assert r.status_code == 200
    videos = _redirect_videos(r)
    assert len(videos) == 1
    assert in_asset_root(ASSETS_ROOT, Path(videos[0]))

    html = client.get(r.json()["redirect"]).text
    assert _prefill(html, "clone-long-form", "data-prefill-video") == videos


def test_a_download_with_no_video_cannot_be_sent(client, tmp_path):
    job = _download_job([_downloaded(tmp_path, "photo.jpg")])

    r = client.post("/download-media/send", data={"job_id": job, "target": "clone"})
    assert r.status_code == 400


def test_another_users_download_cannot_be_sent(client, tmp_path):
    job = _download_job([_downloaded(tmp_path, "a.mp4")], actor="999999")

    r = client.post("/download-media/send", data={"job_id": job, "target": "clone"})
    assert r.status_code == 404


def test_an_unknown_target_is_refused(client, tmp_path):
    job = _download_job([_downloaded(tmp_path, "a.mp4")])

    r = client.post("/download-media/send", data={"job_id": job, "target": "nope"})
    assert r.status_code == 400


def _card(html: str, job_id: str) -> str:
    """The one result card's markup, from its data-job-id to the next card."""
    after = html.split(f'data-job-id="{job_id}"', 1)[1]
    return after.split("data-job-id=", 1)[0]


def test_the_card_carries_the_clone_control_only_for_a_video(client, tmp_path):
    video = _download_job([_downloaded(tmp_path, "a.mp4")])
    image = _download_job([_downloaded(tmp_path, "p.png")])

    html = client.get("/download-assets").text

    assert "send-to-clone" in _card(html, video)
    assert "send-to-long" in _card(html, video)
    assert "send-to-clone" not in _card(html, image)
