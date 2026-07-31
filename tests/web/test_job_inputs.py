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
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.downloads import videos as download_reels_router
from ofmhelpers.web.routers.generation import fake_ai as fake_ai_router


def _png_bytes() -> bytes:
    """A real (Pillow-decodable) PNG -- the reference-preview path now
    thumbnails images, so tests need actual image bytes, not a placeholder
    string."""
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color="red").save(buf, format="PNG")
    return buf.getvalue()


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
    assert long_prompt in html
    assert "inputs-long-text" in html
    assert '<th scope="row">outcome</th>' in html
    assert "<td>success</td>" in html
    assert '<th scope="row">asset type</th>' in html
    assert "<td>image</td>" in html
    assert "reference images" in html
    assert "myref.png" in html


def test_reference_preview_url_actually_serves_the_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    ref_bytes = _png_bytes()
    files = {"reference_images": ("myref.png", io.BytesIO(ref_bytes), "image/png")}
    data = {
        "prompt": "short",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    job_id = client.post("/fake-ai/run", data=data, files=files).json()["job_id"]
    html = client.get(f"/fake-ai/jobs/{job_id}").text

    # Image reference previews render through the cached thumbnail endpoint
    # (see task_helpers.reference_asset), not the full-res original.
    match = re.search(r'/refs/thumb\?path=[^"&]+', html)
    assert match, "no reference thumbnail URL found on the page"

    r = client.get(match.group(0))
    assert r.status_code == 200
    assert r.content != ref_bytes, "should be a resized thumbnail, not the original"


def test_seedance_job_status_shows_settings_and_reference_video(client):
    with mock.patch(
        "ofmhelpers.web.routers.generation.seedance.KieAIClient"
    ) as MockClient:

        def fake_generate(**kwargs):
            kwargs["on_result_urls"](["https://cdn.kie.ai/out/fake.mp4"])
            return Path("/tmp/fake.mp4")

        MockClient.from_env.return_value.generate_video_seedance2.side_effect = (
            fake_generate
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
    assert '<th scope="row">resolution</th>' in html
    assert "<td>720p</td>" in html
    assert "reference videos" in html
    assert "clip.mp4" in html


def test_kling3_job_status_shows_settings(client):
    with mock.patch(
        "ofmhelpers.web.routers.generation.kling.KieAIClient"
    ) as MockClient:

        def fake_generate(**kwargs):
            kwargs["on_result_urls"](["https://cdn.kie.ai/out/fake.mp4"])
            return Path("/tmp/fake.mp4")

        MockClient.from_env.return_value.generate_video_kling3.side_effect = (
            fake_generate
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            job_id = client.post(
                "/kling3/run",
                data={"api_key": "k", "prompt": "kling prompt", "mode": "pro"},
            ).json()["job_id"]

    html = client.get(f"/kling3/jobs/{job_id}").text
    assert '<th scope="row">mode</th>' in html
    assert "<td>pro</td>" in html


def test_nanobanana_job_status_shows_settings(client):
    with mock.patch("ofmhelpers.web.routers.generation.nbp.KieAIClient") as MockClient:

        def fake_generate(**kwargs):
            kwargs["on_result_urls"](["https://cdn.kie.ai/out/fake.png"])
            return Path("/tmp/fake.png")

        MockClient.from_env.return_value.generate_image_nbp.side_effect = fake_generate
        with mock.patch("pathlib.Path.is_file", return_value=True):
            job_id = client.post(
                "/nanobanana/run",
                data={"api_key": "k", "prompt": "nbp prompt", "resolution": "2K"},
            ).json()["job_id"]

    html = client.get(f"/nanobanana/jobs/{job_id}").text
    assert '<th scope="row">resolution</th>' in html
    assert "<td>2K</td>" in html


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
