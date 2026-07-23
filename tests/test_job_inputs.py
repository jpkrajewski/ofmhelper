"""
Covers the "Inputs" section on the AI-generation job-status pages
(/seedance/jobs/{id}, /kling3/jobs/{id}, /nanobanana/jobs/{id},
/fake-ai/jobs/{id}): every input the job actually ran with -- settings, the
full prompt, and previews of any reference files used -- must be visible on
that one page for replicability/debugging.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import re
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.aigenproviders.kaiai.upload_cache import upload_cache
from ofmhelpers.web.routers import download_reels as download_reels_router
from ofmhelpers.web.routers import fake_ai as fake_ai_router


@pytest.fixture(autouse=True)
def _clean_upload_cache():
    upload_cache.clear()
    yield
    upload_cache.clear()


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_fake_ai_job_status_shows_settings_prompt_and_reference_preview(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    long_prompt = "a" * 80  # over the long-text threshold -> its own block
    files = {"reference_images": ("myref.png", io.BytesIO(b"ref bytes"), "image/png")}
    data = {
        "prompt": long_prompt,
        "outcome": "success",
        "asset_type": "image",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    job_id = client.post("/fake-ai/run", data=data, files=files).json()["job_id"]

    html = client.get(f"/fake-ai/jobs/{job_id}").text

    assert "Inputs" in html
    assert "Output" in html
    assert long_prompt in html and "inputs-long-text" in html
    assert "<th>outcome</th>" in html and "<td>success</td>" in html
    assert "<th>asset type</th>" in html and "<td>image</td>" in html
    assert "reference images" in html
    assert "myref.png" in html


def test_reference_preview_url_actually_serves_the_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    files = {"reference_images": ("myref.png", io.BytesIO(b"ref bytes"), "image/png")}
    data = {
        "prompt": "short",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    job_id = client.post("/fake-ai/run", data=data, files=files).json()["job_id"]
    html = client.get(f"/fake-ai/jobs/{job_id}").text

    match = re.search(r'/refs/file\?path=[^"&]+', html)
    assert match, "no reference preview URL found on the page"

    r = client.get(match.group(0))
    assert r.status_code == 200
    assert r.content == b"ref bytes"


def test_seedance_job_status_shows_settings_and_reference_video(client):
    with mock.patch("ofmhelpers.web.routers.seedance.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_video_seedance2.return_value = Path(
            "/tmp/fake.mp4"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            files = {
                "reference_videos": ("clip.mp4", io.BytesIO(b"vid bytes"), "video/mp4")
            }
            data = {
                "api_key": "k",
                "prompt": "seedance prompt",
                "resolution": "720p",
                "reference_videos_manifest": '[{"kind": "new"}]',
            }
            job_id = client.post("/seedance/run", data=data, files=files).json()[
                "job_id"
            ]

    html = client.get(f"/seedance/jobs/{job_id}").text
    assert "<th>resolution</th>" in html and "<td>720p</td>" in html
    assert "reference videos" in html
    assert "clip.mp4" in html


def test_kling3_job_status_shows_settings(client):
    with mock.patch("ofmhelpers.web.routers.kling.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_video_kling3.return_value = Path(
            "/tmp/fake.mp4"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            job_id = client.post(
                "/kling3/run",
                data={"api_key": "k", "prompt": "kling prompt", "mode": "pro"},
            ).json()["job_id"]

    html = client.get(f"/kling3/jobs/{job_id}").text
    assert "<th>mode</th>" in html and "<td>pro</td>" in html


def test_nanobanana_job_status_shows_settings(client):
    with mock.patch("ofmhelpers.web.routers.nbp.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.return_value = Path(
            "/tmp/fake.png"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            job_id = client.post(
                "/nanobanana/run",
                data={"api_key": "k", "prompt": "nbp prompt", "resolution": "2K"},
            ).json()["job_id"]

    html = client.get(f"/nanobanana/jobs/{job_id}").text
    assert "<th>resolution</th>" in html and "<td>2K</td>" in html


def test_non_ai_gen_job_status_page_has_no_inputs_section(client, monkeypatch):
    """job_inputs is only passed by the 4 AI-gen routers -- since job_status.html
    is shared by every job type, confirm a plain download job doesn't grow a
    stray "Inputs" section it was never given."""
    monkeypatch.setattr(download_reels_router, "_run_downloads", lambda urls: [])
    job_id = client.post(
        "/download-videos/run", data={"urls": "https://a.example/1"}
    ).json()["job_id"]

    html = client.get(f"/download-videos/jobs/{job_id}").text
    assert "job-inputs" not in html
