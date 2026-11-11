"""/reels/clone and /reels/download leave no job folder behind, whether the
downloads worked or not; the videos /clone opens with stay in the asset store."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.reels import page as reels
from ofmhelpers.web.main import app

CDN = "https://scontent.cdninstagram.com/{}.mp4"
PICKED = json.dumps(
    [
        {"code": "A1", "video_url": CDN.format("a")},
        {"code": "B2", "video_url": CDN.format("b")},
    ]
)


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "uploads"
    monkeypatch.setattr(reels, "uploads_root", lambda: root)
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: tmp_path / "assets")
    return root


@pytest.fixture
def client(uploads) -> TestClient:
    c = TestClient(app, raise_server_exceptions=False)
    sign_in_agency(c)
    return c


def _downloads_work(monkeypatch) -> None:
    def fake(_self, url, dest: Path):
        dest.write_bytes(url.encode())
        return dest

    monkeypatch.setattr(reels.InstagramClient, "download_video", fake)


def _second_download_fails_with(monkeypatch, error: Exception) -> None:
    def fake(_self, url, dest: Path):
        dest.write_bytes(url.encode())
        if "b.mp4" in url:
            raise error
        return dest

    monkeypatch.setattr(reels.InstagramClient, "download_video", fake)


def _job_folders(uploads: Path) -> list[Path]:
    return list((uploads / "reels").iterdir())


def test_clone_leaves_no_folder_and_keeps_its_videos(client, uploads, monkeypatch):
    _downloads_work(monkeypatch)

    r = client.post("/reels/clone", data={"reels": PICKED})

    assert r.status_code == 200, r.text
    videos = parse_qs(urlsplit(r.json()["redirect"]).query)["video"]
    assert [Path(v).read_bytes() for v in videos] == [
        CDN.format("a").encode(),
        CDN.format("b").encode(),
    ]
    assert _job_folders(uploads) == []


@pytest.mark.parametrize("url", ["/reels/clone", "/reels/download"])
def test_a_failed_download_leaves_no_folder(client, uploads, monkeypatch, url):
    _second_download_fails_with(monkeypatch, requests.ConnectionError("cut off"))

    r = client.post(url, data={"reels": PICKED})

    assert r.status_code == 502
    assert r.json() == {
        "detail": "Could not download a reel — rescan the profile and retry"
    }
    assert _job_folders(uploads) == []


def test_a_crashed_download_leaves_no_folder(client, uploads, monkeypatch):
    _second_download_fails_with(monkeypatch, ValueError("unexpected"))

    r = client.post("/reels/clone", data={"reels": PICKED})

    assert r.status_code == 500
    assert _job_folders(uploads) == []
