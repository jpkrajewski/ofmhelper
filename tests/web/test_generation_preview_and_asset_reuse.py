"""
Covers two behaviors added to the nanobanana/kling3/seedance job runners:

1. A generated asset is registered into the shared uploads/assets store
   (task_helpers.register_generated_asset) the moment its job finishes, so it
   shows up in the "reuse an uploaded ..." picker (/refs) immediately --
   generated output used to live only in kieai_out/, a tree /refs never
   looked at.
2. kie.ai's hosted result URL is published as the job's "preview" the
   instant the poll succeeds -- before the (potentially slow) local download
   starts -- and if that download then fails, the job still finishes "done"
   serving that remote_url forever instead of being marked "failed" (never
   leave the asset in a broken state).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.db.repository import JobRepository
from ofmhelpers.web.jobs import create_job, get_job, run_job
from ofmhelpers.web.main import app
from ofmhelpers.web.routers import kling as kling_router
from ofmhelpers.web.routers import nbp as nbp_router
from ofmhelpers.web.routers import refs as refs_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


NBP_KWARGS = {
    "api_key": "k",
    "prompt": "p",
    "aspect_ratio": "1:1",
    "resolution": "1K",
    "output_format": "png",
    "image_input_paths": [],
}

KLING_KWARGS = {
    "api_key": "k",
    "prompt": "p",
    "mode": "pro",
    "aspect_ratio": "16:9",
    "duration": "5",
    "sound": True,
    "image_paths": [],
}


# ---------------------------------------------------------------------------
# Issue 1: generated output lands in the shared reuse-picker store
# ---------------------------------------------------------------------------


def test_nanobanana_output_registers_into_the_shared_assets_store(
    tmp_path, monkeypatch
):
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(nbp_router, "ASSETS_ROOT", assets_dir)

    out = tmp_path / "out" / "abc123.png"
    out.parent.mkdir()
    out.write_bytes(b"generated pixels")

    job_id = create_job("nanobanana", {"prompt": "p"})

    def fake_generate(**kwargs):
        kwargs["on_result_urls"](["https://cdn.kie.ai/out/abc123.png"])
        return out

    with mock.patch.object(nbp_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.side_effect = fake_generate
        result = nbp_router._run_nanobanana(job_id=job_id, **NBP_KWARGS)

    assert result == [
        {
            "name": "abc123.png",
            "path": str(out),
            "remote_url": "https://cdn.kie.ai/out/abc123.png",
        }
    ]
    saved = list(assets_dir.iterdir())
    assert len(saved) == 1
    assert saved[0].name.endswith("__abc123.png")


def test_generated_asset_shows_up_in_the_reuse_picker_immediately(
    client, tmp_path, monkeypatch
):
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(nbp_router, "ASSETS_ROOT", assets_dir)
    monkeypatch.setattr(refs_router, "ASSETS_ROOT", assets_dir)

    out = tmp_path / "out" / "fresh.png"
    out.parent.mkdir()
    out.write_bytes(b"generated pixels")

    job_id = create_job("nanobanana", {"prompt": "p"})

    def fake_generate(**kwargs):
        kwargs["on_result_urls"](["https://cdn.kie.ai/out/fresh.png"])
        return out

    with mock.patch.object(nbp_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.side_effect = fake_generate
        nbp_router._run_nanobanana(job_id=job_id, **NBP_KWARGS)

    names = [f["name"] for f in client.get("/refs?kind=image").json()]
    assert "fresh.png" in names


# ---------------------------------------------------------------------------
# Issue 2: preview published before download, download failure never fails
# the job
# ---------------------------------------------------------------------------


def test_nanobanana_publishes_a_preview_as_soon_as_the_poll_succeeds(tmp_path):
    job_id = create_job("nanobanana", {"prompt": "p"})

    def fake_generate(**kwargs):
        kwargs["on_result_urls"](["https://cdn.kie.ai/out/abc.png"])
        out = tmp_path / "abc.png"
        out.write_bytes(b"bytes")
        return out

    with mock.patch.object(nbp_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.side_effect = fake_generate
        nbp_router._run_nanobanana(job_id=job_id, **NBP_KWARGS)

    assert get_job(job_id)["preview"] == {
        "remote_url": "https://cdn.kie.ai/out/abc.png",
        "kind": "image",
    }


def test_nanobanana_download_failure_keeps_the_job_done_with_remote_url_only():
    """The generation itself succeeded (kie.ai returned a real result URL) --
    only the local copy failed. The job must still finish "done", serving
    that remote_url, never "failed"."""
    job_id = create_job("nanobanana", {"prompt": "p"})

    def fake_generate(**kwargs):
        kwargs["on_result_urls"](["https://cdn.kie.ai/out/xyz.png"])
        msg = "connection reset mid-download"
        raise ConnectionError(msg)

    with mock.patch.object(nbp_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.side_effect = fake_generate
        run_job(
            job_id,
            nbp_router._run_nanobanana,
            {"job_id": job_id, **NBP_KWARGS},
        )

    assert get_job(job_id)["status"] == "done"
    assert get_job(job_id)["result"] == [
        {
            "name": "xyz.png",
            "path": None,
            "remote_url": "https://cdn.kie.ai/out/xyz.png",
        }
    ]


def test_nanobanana_failure_before_any_result_url_still_fails_the_job():
    """No hosted result was ever obtained (e.g. a bad API key) -- this is a
    real failure and must still be reported as one."""
    job_id = create_job("nanobanana", {"prompt": "p"})

    with mock.patch.object(nbp_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.side_effect = RuntimeError(
            "Wrong API Key"
        )
        run_job(job_id, nbp_router._run_nanobanana, {"job_id": job_id, **NBP_KWARGS})

    assert get_job(job_id)["status"] == "failed"
    assert get_job(job_id)["error"] == "Wrong API Key"


def test_kling3_download_failure_keeps_the_job_done_with_remote_url_only():
    job_id = create_job("kling3", {"prompt": "p"})

    def fake_generate(**kwargs):
        kwargs["on_result_urls"](["https://cdn.kie.ai/out/clip.mp4"])
        msg = "connection reset mid-download"
        raise ConnectionError(msg)

    with mock.patch.object(kling_router, "KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_video_kling3.side_effect = (
            fake_generate
        )
        run_job(job_id, kling_router._run_kling3, {"job_id": job_id, **KLING_KWARGS})

    assert get_job(job_id)["status"] == "done"
    assert get_job(job_id)["result"] == [
        {
            "name": "clip.mp4",
            "path": None,
            "remote_url": "https://cdn.kie.ai/out/clip.mp4",
        }
    ]


def test_job_status_json_surfaces_preview_only_while_running(tmp_path):
    job_id = create_job("nanobanana", {"prompt": "p"})
    from ofmhelpers.web.jobs import set_job_preview
    from ofmhelpers.web.routers.task_helpers import job_status_payload

    set_job_preview(job_id, {"remote_url": "https://cdn.kie.ai/x.png", "kind": "image"})

    running_payload = job_status_payload(get_job(job_id), "/nanobanana/files")
    assert running_payload["preview"] == {
        "remote_url": "https://cdn.kie.ai/x.png",
        "kind": "image",
    }

    (tmp_path / "x.png").write_bytes(b"x")
    JobRepository().update_status(
        job_id, "done", result=[{"name": "x.png", "path": str(tmp_path / "x.png")}]
    )
    done_payload = job_status_payload(get_job(job_id), "/nanobanana/files")
    assert "preview" not in done_payload
