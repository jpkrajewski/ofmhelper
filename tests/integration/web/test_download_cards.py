"""/download-assets: every file of a download is its own card, reloaded as when
it finishes on the page, and a card's Clone sends that card's video."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.web.main import app


@pytest.fixture
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: root)
    return root


@pytest.fixture
def client(assets) -> TestClient:
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _download(client, tmp_path: Path, *names: str, failed: str | None = None) -> str:
    """A finished download_media job of those files, all from one link."""
    folder = tmp_path / "downloads"
    folder.mkdir(exist_ok=True)
    paths = []
    for name in names:
        (folder / name).write_bytes(name.encode())
        paths.append(folder / name)
    result = [DownloadResult(url="https://x/1", success=True, output_paths=paths)]
    if failed:
        result.append(DownloadResult(url=failed, success=False, error="gone"))
    job_id = create_job(
        "download_media", {"urls": ["https://x/1"]}, actor=str(client.user.id)
    )
    run_job(job_id, lambda r=result: r, {})
    return job_id


def _cards(html: str, job_id: str) -> list[str]:
    return html.split(f'data-job-id="{job_id}"')[1:]


def _sent(response) -> list[str]:
    return parse_qs(urlsplit(response.json()["redirect"]).query)["video"]


def _stored(assets: Path) -> list[Path]:
    return [p for p in assets.rglob("*") if p.is_file()]


@pytest.mark.parametrize("url", ["/download-assets", "/download-assets/gallery"])
def test_every_file_is_its_own_card(client, tmp_path, url):
    job = _download(client, tmp_path, "a.mp4", "b.mp4", "c.mp4")

    html = client.get(url).text

    cards = _cards(html, job)
    assert len(cards) == 3
    for index, card in enumerate(cards):
        assert f'data-file-index="{index}"' in card
    assert f"/download-media/files/{job}/2" in cards[2]
    assert f"/download-media/files/{job}/0" not in cards[2]
    assert "files</a>" not in html


def test_one_file_is_one_card(client, tmp_path):
    job = _download(client, tmp_path, "a.mp4")

    assert len(_cards(client.get("/download-assets").text, job)) == 1


def test_what_failed_shows_once(client, tmp_path):
    _download(client, tmp_path, "a.mp4", "b.mp4", failed="https://x/2")

    assert client.get("/download-assets").text.count("1 link failed") == 1


def test_only_a_video_card_can_be_sent_to_clone(client, tmp_path):
    job = _download(client, tmp_path, "a.mp4", "b.jpg")

    video, image = _cards(client.get("/download-assets/gallery").text, job)

    assert "send-to-clone" in video
    assert "send-to-clone" not in image


@pytest.mark.parametrize("target", ["clone", "clone-long"])
def test_a_card_sends_its_own_video_and_stores_only_that(
    client, tmp_path, assets, target
):
    job = _download(client, tmp_path, "a.mp4", "b.mp4", "c.mp4")

    r = client.post(
        "/download-media/send", data={"job_id": job, "target": target, "index": "1"}
    )

    assert r.status_code == 200, r.text
    assert [Path(v).name.split("__", 1)[1] for v in _sent(r)] == ["b.mp4"]
    assert len(_stored(assets)) == 1


def test_clone_long_stores_only_the_video_it_opens_with(client, tmp_path, assets):
    job = _download(client, tmp_path, "a.mp4", "b.mp4", "c.mp4")

    r = client.post(
        "/download-media/send", data={"job_id": job, "target": "clone-long"}
    )

    assert len(_sent(r)) == 1
    assert len(_stored(assets)) == 1


def test_no_card_index_still_sends_every_video_to_clone(client, tmp_path):
    job = _download(client, tmp_path, "a.mp4", "b.mp4")

    r = client.post("/download-media/send", data={"job_id": job, "target": "clone"})

    assert len(_sent(r)) == 2


def test_an_image_card_or_a_missing_file_has_no_video_to_send(client, tmp_path):
    job = _download(client, tmp_path, "a.jpg", "b.mp4")

    for index in ("0", "9"):
        r = client.post(
            "/download-media/send",
            data={"job_id": job, "target": "clone", "index": index},
        )
        assert r.status_code == 400
        assert r.json() == {"detail": "This download has no video"}


def test_a_negative_index_is_refused(client, tmp_path):
    job = _download(client, tmp_path, "a.mp4")

    r = client.post(
        "/download-media/send", data={"job_id": job, "target": "clone", "index": "-1"}
    )

    assert r.status_code == 422
