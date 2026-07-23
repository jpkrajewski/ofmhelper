"""
Covers the Fake AI Model testing tool: it must behave like a real kie.ai
model for output/upload *plumbing* purposes (same OFM_KIEAI_OUT_DIR as
KieAIClient.from_env, same shared uploads/assets store for reference
uploads) while never making a real network call -- so this can all be
verified without spending API credits.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.jobs import JOBS
from ofmhelpers.web.routers import fake_ai as fake_ai_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_success_image_writes_into_the_shared_kieai_out_dir(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    r = client.post(
        "/fake-ai/run",
        data={
            "prompt": "a test prompt",
            "outcome": "success",
            "asset_type": "image",
            "delay": "0",
        },
    )
    job = JOBS[r.json()["job_id"]]

    assert job["status"] == "done"
    out_path = Path(job["result"][0]["path"])
    assert out_path.parent == tmp_path
    assert out_path.is_file()


def test_error_outcome_fails_with_the_exact_message_typed_in(client):
    r = client.post(
        "/fake-ai/run",
        data={
            "prompt": "p",
            "outcome": "error",
            "error_message": "boom, on purpose",
            "delay": "0",
        },
    )
    job = JOBS[r.json()["job_id"]]

    assert job["status"] == "failed"
    assert job["error"] == "boom, on purpose"  # not a raw traceback


def test_video_outcome_shells_out_to_ffmpeg(client, tmp_path, monkeypatch):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    with mock.patch("ofmhelpers.web.routers.fake_ai.subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0)
        r = client.post(
            "/fake-ai/run",
            data={
                "prompt": "p",
                "outcome": "success",
                "asset_type": "video",
                "delay": "0",
            },
        )
        job = JOBS[r.json()["job_id"]]

    assert job["status"] == "done"
    assert job["result"][0]["name"].endswith(".mp4")
    assert mock_run.called
    assert mock_run.call_args[0][0][0] == "ffmpeg"
    # the intermediate PNG frame ffmpeg reads from is cleaned up afterward
    assert list(tmp_path.glob("*.png")) == []


def test_video_outcome_without_ffmpeg_installed_fails_cleanly(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    with mock.patch(
        "ofmhelpers.web.routers.fake_ai.subprocess.run", side_effect=FileNotFoundError()
    ):
        r = client.post(
            "/fake-ai/run",
            data={
                "prompt": "p",
                "outcome": "success",
                "asset_type": "video",
                "delay": "0",
            },
        )
        job = JOBS[r.json()["job_id"]]

    assert job["status"] == "failed"
    assert "ffmpeg" in job["error"]


def test_reference_upload_dedupes_into_the_shared_assets_store(
    client, tmp_path, monkeypatch
):
    # Separate dirs for the ref store vs. generation output -- otherwise the
    # job's own generated PNG would land alongside the refs and confuse the
    # "only stored once" count below.
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(fake_ai_router, "ASSETS_ROOT", assets_dir)
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path / "out")

    files = {
        "reference_images": ("shared.png", io.BytesIO(b"identical bytes"), "image/png")
    }
    data = {
        "prompt": "p",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    client.post("/fake-ai/run", data=data, files=files)

    files2 = {
        "reference_images": ("renamed.png", io.BytesIO(b"identical bytes"), "image/png")
    }
    client.post("/fake-ai/run", data=data, files=files2)

    saved = list(assets_dir.iterdir())
    assert len(saved) == 1, "same content uploaded twice should only be stored once"


def test_uploaded_ref_paths_are_stored_in_job_params_for_click_to_reuse(
    client, tmp_path, monkeypatch
):
    """The /generate gallery's click-to-reuse restores a past job's reference
    files by reading their server paths out of the job's stored params -- so
    those paths must actually be recorded there, keyed by the form's picker
    field name."""
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(fake_ai_router, "ASSETS_ROOT", assets_dir)
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path / "out")

    files = {"reference_images": ("ref.png", io.BytesIO(b"ref bytes"), "image/png")}
    data = {
        "prompt": "p",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    r = client.post("/fake-ai/run", data=data, files=files)
    params = JOBS[r.json()["job_id"]]["params"]

    assert len(params["reference_images"]) == 1
    stored_path = Path(params["reference_images"][0])
    assert stored_path.is_file()
    assert stored_path.parent == assets_dir
    assert params["reference_videos"] == []
    assert params["reference_audio"] == []


def test_restored_existing_ref_round_trips_without_reupload(
    client, tmp_path, monkeypatch
):
    """Simulates the click-to-reuse resubmit: the browser sends no bytes, just
    an "existing" manifest entry with the path from a past job's params. The
    server must resolve it to the same file -- no new copy on disk."""
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(fake_ai_router, "ASSETS_ROOT", assets_dir)
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path / "out")

    files = {"reference_images": ("ref.png", io.BytesIO(b"ref bytes"), "image/png")}
    data = {
        "prompt": "p",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": '[{"kind": "new"}]',
    }
    r = client.post("/fake-ai/run", data=data, files=files)
    original_path = JOBS[r.json()["job_id"]]["params"]["reference_images"][0]

    # Resubmit referencing the stored path -- no file attached this time.
    data2 = {
        "prompt": "p again",
        "outcome": "success",
        "delay": "0",
        "reference_images_manifest": json.dumps(
            [{"kind": "existing", "path": original_path}]
        ),
    }
    r2 = client.post("/fake-ai/run", data=data2)
    params2 = JOBS[r2.json()["job_id"]]["params"]

    assert params2["reference_images"] == [original_path]
    assert len(list(assets_dir.iterdir())) == 1, "reuse must not create a second copy"
