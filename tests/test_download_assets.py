"""
Covers the unified /download-assets page (Download videos / Download images /
Clean images collapsed into one non-blocking page) and the Action log's
"who did what" tracking -- all without touching the network (the actual
downloaders are stubbed).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import re

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.jobs import JOBS, create_job
from ofmhelpers.web.routers import clean_image as clean_image_router
from ofmhelpers.web.routers import download_reels as download_reels_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_download_assets_page_renders_all_three_tools(client):
    html = client.get("/download-assets").text
    for tool in ("download_videos", "download_images", "clean_images"):
        assert f'data-tool="{tool}"' in html
    assert 'id="tool-select"' in html
    # old standalone form pages are gone
    assert client.get("/download-videos").status_code in (404, 405)
    assert client.get("/download-images").status_code in (404, 405)
    assert client.get("/clean-images").status_code in (404, 405)


def test_download_videos_json_run_and_grouped_status(client, monkeypatch, tmp_path):
    out_file = tmp_path / "clip.mp4"
    out_file.write_bytes(b"fake video")

    def fake_downloads(urls):
        return [
            {
                "url": urls[0],
                "success": True,
                "output_paths": [str(out_file)],
                "error": None,
            },
            {
                "url": urls[1],
                "success": False,
                "output_paths": [],
                "error": "404 not found",
            },
        ]

    monkeypatch.setattr(download_reels_router, "_run_downloads", fake_downloads)

    r = client.post(
        "/download-videos/run",
        data={"urls": "https://a.example/1\nhttps://b.example/2"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    status = client.get(f"/download-videos/jobs/{job_id}/status").json()
    assert status["status"] == "done"
    assert len(status["result"]) == 1
    assert status["result"][0]["kind"] == "video"
    assert status["result"][0]["source"] == "https://a.example/1"
    assert status["failed_sources"] == [
        {"source": "https://b.example/2", "error": "404 not found"}
    ]

    # the file behind the card's view_url actually serves
    assert client.get(status["result"][0]["view_url"]).status_code == 200
    # HTML deep link still renders via the shared status template
    assert client.get(f"/download-videos/jobs/{job_id}").status_code == 200


def test_clean_images_json_run_and_status(client, monkeypatch, tmp_path):
    monkeypatch.setattr(clean_image_router, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(clean_image_router, "clean_metadata", lambda d: None)

    files = [("files", ("photo.png", io.BytesIO(b"img bytes"), "image/png"))]
    r = client.post("/clean-images/run", files=files)
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    status = client.get(f"/clean-images/jobs/{job_id}/status").json()
    assert status["status"] == "done"
    assert status["result"][0]["name"] == "photo.png"
    assert status["result"][0]["kind"] == "image"


def test_empty_submissions_rejected_cleanly(client):
    r = client.post("/download-videos/run", data={"urls": "  \n  "})
    assert r.status_code == 400
    r = client.post("/clean-images/run")
    assert r.status_code == 400


def test_action_log_records_who_ran_what(client, monkeypatch, tmp_path):
    monkeypatch.setattr(download_reels_router, "_run_downloads", lambda urls: [])

    # admin runs one
    r = client.post("/download-videos/run", data={"urls": "https://a.example/1"})
    admin_job = r.json()["job_id"]
    assert JOBS[admin_job]["actor"] == "admin"

    # va runs one
    va_client = TestClient(app)
    va_client.post("/login", data={"password": "test-va", "next": "/"})
    r = va_client.post("/download-videos/run", data={"urls": "https://a.example/2"})
    va_job = r.json()["job_id"]
    assert JOBS[va_job]["actor"] == "va"

    html = client.get("/jobs").text
    assert "Action log" in html
    assert 'actor-admin">admin' in html
    assert 'actor-va">va' in html


def test_still_running_card_carries_poll_attributes_on_page_reload(client):
    """Same regression as /generate: leaving /download-assets while a job is
    still running and coming back must not leave the spinner stuck --
    the server-rendered card needs data-pending + data-poll-prefix so
    generation.js resumes polling it instead of requiring a manual refresh."""
    job_id = create_job("download_videos", {"urls": ["https://a.example/1"]})
    assert JOBS[job_id]["status"] == "running"

    html = client.get("/download-assets").text
    card = re.search(rf'data-job-id="{job_id}"(.*?)>', html, re.S)
    assert card, f"no gallery card for job {job_id}"
    assert "data-pending" in card.group(1)
    assert 'data-poll-prefix="/download-videos"' in card.group(1)
