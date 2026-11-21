import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import sign_in, sign_in_agency

from ofmhelpers.db.jobs import store as jobs_store
from ofmhelpers.db.jobs.store import create_job, get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.downloads import clean as clean_image_router
from ofmhelpers.features.downloads import images as download_images_router
from ofmhelpers.features.downloads import videos as download_reels_router
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.downloaders.generic import DownloadResult
from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
    return c


def test_download_assets_page_offers_one_download_tool_and_clean(client):
    html = client.get("/download-assets").text
    for tool in ("download_media", "clean_images"):
        assert f'data-tool="{tool}"' in html
    for gone in ("download_videos", "download_images"):
        assert f'data-tool="{gone}"' not in html
    assert 'action="/download-media/run"' in html
    assert 'id="tool-select"' in html
    assert client.get("/download-videos").status_code in (404, 405)
    assert client.get("/download-images").status_code in (404, 405)
    assert client.get("/clean-images").status_code in (404, 405)


def test_download_videos_json_run_and_grouped_status(client, monkeypatch, tmp_path):
    out_file = tmp_path / "clip.mp4"
    out_file.write_bytes(b"fake video")

    def fake_downloads(urls, assets_root, user_id=None, job_id=None):
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

    monkeypatch.setattr(download_reels_router, "run_downloads", fake_downloads)

    r = client.post(
        "/download-videos/run",
        data={"urls": "https://a.example/1\nhttps://b.example/2"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = client.get(f"/download-videos/jobs/{job_id}/status").json()
    assert status["status"] == "done"
    assert len(status["result"]) == 1
    assert status["result"][0]["kind"] == "video"
    assert status["result"][0]["source"] == "https://a.example/1"
    assert status["failed_sources"] == [
        {
            "source": "https://b.example/2",
            "error": "404 not found",
            "link": "b.example/2",
            "reason": "Not found: the post is gone or the link is wrong",
        }
    ]

    assert client.get(status["result"][0]["view_url"]).status_code == 200
    assert client.get(f"/download-videos/jobs/{job_id}").status_code == 200


def _png(color: str) -> io.BytesIO:
    image = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(image, "PNG")
    image.seek(0)
    return image


def test_clean_images_json_run_and_status(client, monkeypatch, tmp_path):
    monkeypatch.setattr(clean_image_router, "upload_root", lambda: tmp_path)

    files = [("files", ("photo.png", _png("red"), "image/png"))]
    r = client.post("/clean-images/run", files=files)
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = client.get(f"/clean-images/jobs/{job_id}/status").json()
    assert status["status"] == "done"
    assert status["result"][0]["name"] == "IMG_0001.png"
    assert status["result"][0]["kind"] == "image"


def test_clean_images_keeps_two_images_with_one_name(client, monkeypatch, tmp_path):
    monkeypatch.setattr(clean_image_router, "upload_root", lambda: tmp_path)

    files = [
        ("files", ("image.png", _png("red"), "image/png")),
        ("files", ("image.png", _png("blue"), "image/png")),
    ]
    r = client.post("/clean-images/run", files=files)

    status = client.get(f"/clean-images/jobs/{r.json()['job_id']}/status").json()
    assert [f["name"] for f in status["result"]] == ["IMG_0001.png", "IMG_0002.png"]


def test_a_cleaned_image_saves_under_a_name_of_its_own_job(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(clean_image_router, "upload_root", lambda: tmp_path)

    def cleaned() -> tuple[str, dict]:
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(image, "PNG")
        image.seek(0)
        files = [("files", ("photo.png", image, "image/png"))]
        job = client.post("/clean-images/run", files=files).json()["job_id"]
        (card,) = client.get(f"/clean-images/jobs/{job}/status").json()["result"]
        return job, card

    for job, card in (cleaned(), cleaned()):
        assert card["name"] == "IMG_0001.png"
        assert card["download_name"] == download_name("IMG_0001.png", job)
        saved = client.get(card["download_url"]).headers["content-disposition"]
        assert f'filename="{card["download_name"]}"' in saved


def test_clean_images_offers_the_adding_picker_without_reuse(client):
    html = client.get("/download-assets").text
    fieldset = html.split('data-tool="clean_images"')[1].split("</fieldset>")[0]
    assert 'data-field="files"' in fieldset
    assert "data-required" in fieldset
    assert 'class="file-add-input" multiple' in fieldset
    assert "ref-toggle" not in fieldset
    assert "js/file-picker.js" in html


def test_empty_submissions_rejected_cleanly(client):
    r = client.post("/download-videos/run", data={"urls": "  \n  "})
    assert r.status_code == 400
    r = client.post("/clean-images/run")
    assert r.status_code == 400


def test_action_log_records_who_ran_what(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_reels_router,
        "run_downloads",
        lambda urls, assets_root, user_id=None, job_id=None: [],
    )

    r = client.post("/download-videos/run", data={"urls": "https://a.example/1"})
    admin_job = r.json()["job_id"]
    assert get_job(admin_job).actor == str(users.by_email("admin").id)

    user_client = TestClient(app)
    user = sign_in(user_client)
    r = user_client.post("/download-videos/run", data={"urls": "https://a.example/2"})
    user_job = r.json()["job_id"]
    assert get_job(user_job).actor == str(user.id)

    html = client.get("/action-log").text
    assert "<h1>Logs</h1>" in html
    assert 'class="badge badge-neutral">admin<' in html
    assert f'class="badge badge-neutral">{user.email}<' in html
    assert f"/jobs/{user_job}" not in html
    assert f"/jobs/{admin_job}" in html


def test_still_running_card_carries_poll_attributes_on_page_reload(client):
    job_id = create_job("download_videos", {"urls": ["https://a.example/1"]})
    assert get_job(job_id).status == "running"

    html = client.get("/download-assets").text
    card = re.search(rf'data-job-id="{job_id}"(.*?)>', html, re.DOTALL)
    assert card, f"no gallery card for job {job_id}"
    assert "data-pending" in card.group(1)
    assert 'data-poll-prefix="/download-videos"' in card.group(1)


def test_run_downloads_stringifies_output_paths(monkeypatch, tmp_path):
    out_file = tmp_path / "clip.mp4"
    out_file.write_bytes(b"fake video")

    def fake_download_all(urls, **_login):
        return [DownloadResult(url=urls[0], success=True, output_paths=[out_file])]

    monkeypatch.setattr(download_reels_router, "download_all", fake_download_all)

    result = download_reels_router.run_downloads(
        ["https://a.example/1"], str(tmp_path / "assets")
    )

    stored = jobs_store._stored(result)
    assert stored[0]["output_paths"] == [str(out_file)]
    json.dumps(stored)


def test_run_downloads_images_stringifies_output_paths(monkeypatch, tmp_path):
    out_file = tmp_path / "pic.png"
    out_file.write_bytes(b"fake image")

    def fake_download_all(urls, **_login):
        return [DownloadResult(url=urls[0], success=True, output_paths=[out_file])]

    monkeypatch.setattr(download_images_router, "download_all", fake_download_all)

    result = download_images_router.run_downloads(
        ["https://a.example/1"], str(tmp_path / "assets")
    )

    stored = jobs_store._stored(result)
    assert stored[0]["output_paths"] == [str(out_file)]
    json.dumps(stored)


def test_a_bad_job_result_cannot_break_future_jobs():
    poisoned_job = create_job("download_videos", {"urls": ["https://a.example/1"]})
    run_job(poisoned_job, lambda: [{"path": Path("/tmp/gone.mp4")}], {})
    assert get_job(poisoned_job).status == "failed"

    next_job = create_job("nanobanana", {"prompt": "a cat"})
    assert get_job(next_job).task == "nanobanana"
