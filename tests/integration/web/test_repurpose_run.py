"""/repurpose/run: what its form takes. The knobs are one JSON string the route
parses, so a value smuggled into the ffmpeg filtergraph through `noise_flags`
is refused before anything runs."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.routers.video_tools import repurpose

CLIP = ("video", ("clip.mp4", b"not really a video"))
NEW = json.dumps([{"kind": "new"}])


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path / "assets")
    monkeypatch.setattr(repurpose, "UPLOAD_ROOT", tmp_path / "repurpose")


@pytest.fixture
def queued(monkeypatch):
    calls: list[dict] = []

    def enqueue(_run_job, _job_id, _fn, kwargs, **_options):
        calls.append(kwargs)

    monkeypatch.setattr(repurpose, "enqueue", enqueue)
    return calls


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in(c)
    return c


def _run(client, **data):
    return client.post(
        "/repurpose/run",
        data={"video_manifest": NEW, **data},
        files=[CLIP],
    )


@pytest.mark.parametrize("knob", ["video", "video_special"])
def test_a_filter_smuggled_into_a_knob_is_refused(client, queued, knob):
    preset = json.dumps({knob: {"noise_flags": "t:movie=/etc/passwd"}})

    r = _run(client, preset=preset)

    assert r.status_code == 400
    assert r.json() == {"detail": "A knob has a value it cannot take"}
    assert queued == []


def test_knobs_that_are_not_json_are_refused(client, queued):
    r = _run(client, preset="{not json")

    assert r.status_code == 400
    assert queued == []


def test_a_video_with_blank_settings_queues_one_job_on_the_defaults(client, queued):
    r = _run(client, preset="", preset_name="", device="", copies="", special="")

    assert r.status_code == 200, r.text
    assert len(queued) == 1
    assert queued[0]["device"] == repurpose.DEFAULT_DEVICE
    assert queued[0]["copies"] == repurpose.DEFAULT_COPIES
    assert queued[0]["special"] is False


def test_an_unknown_device_is_refused(client, queued):
    r = _run(client, device="   ")

    assert r.status_code == 400
    assert r.json() == {"detail": "Unknown device"}
    assert queued == []


def test_no_video_is_refused(client, queued):
    r = client.post("/repurpose/run", data={"preset": "{}"})

    assert r.status_code == 400
    assert r.json() == {"detail": "Pick one video"}
    assert queued == []
