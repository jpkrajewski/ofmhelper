"""
Verifies the /generate page's "click a past generation to reload its
settings" feature will actually find a matching field for every parameter
each tool stores -- for all four tools (seedance, kling3, nanobanana,
fake_ai). This mirrors exactly what the click handler in generate_form.html
does at runtime: for every key in a job's stored params (except "prompt",
which maps to the shared textarea), a scalar value looks up `[name="{key}"]`
inside that tool's `<fieldset data-tool="...">` block, and a list value
(reference file paths) looks up a `.file-picker[data-field="{key}"]` widget
to restore the files into.

Written so this can be checked by running the suite instead of clicking
through the browser and spending real API credits on each tool.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.jobs import JOBS, create_job
from ofmhelpers.web.routers import fake_ai as fake_ai_router


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _fieldset_html(html: str, tool: str) -> str:
    match = re.search(
        rf'<fieldset[^>]*data-tool="{tool}"[^>]*>(.*?)</fieldset>', html, re.S
    )
    assert match, f'no <fieldset data-tool="{tool}"> found in /generate'
    return match.group(1)


def _fieldset_field_names(html: str, tool: str) -> set[str]:
    return set(re.findall(r'name="([a-zA-Z_]+)"', _fieldset_html(html, tool)))


def _fieldset_picker_fields(html: str, tool: str) -> set[str]:
    return set(re.findall(r'data-field="([a-zA-Z_]+)"', _fieldset_html(html, tool)))


def _submit_seedance(client):
    with mock.patch("ofmhelpers.web.routers.seedance.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_video_seedance2.return_value = Path(
            "/tmp/fake.mp4"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            r = client.post(
                "/seedance/run",
                data={"api_key": "k", "prompt": "p", "resolution": "720p"},
            )
    return r.json()["job_id"]


def _submit_kling3(client):
    with mock.patch("ofmhelpers.web.routers.kling.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_video_kling3.return_value = Path(
            "/tmp/fake.mp4"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            r = client.post("/kling3/run", data={"api_key": "k", "prompt": "p"})
    return r.json()["job_id"]


def _submit_nanobanana(client):
    with mock.patch("ofmhelpers.web.routers.nbp.KieAIClient") as MockClient:
        MockClient.from_env.return_value.generate_image_nbp.return_value = Path(
            "/tmp/fake.png"
        )
        with mock.patch("pathlib.Path.is_file", return_value=True):
            r = client.post("/nanobanana/run", data={"api_key": "k", "prompt": "p"})
    return r.json()["job_id"]


def _submit_fake_ai(client, tmp_path, monkeypatch):
    # Fake AI Model actually runs for real (nothing to mock) -- redirect its
    # output away from the real OFM_KIEAI_OUT_DIR default so this test can't
    # write outside the project (or into real generation output) by accident.
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)
    r = client.post(
        "/fake-ai/run",
        data={"prompt": "p", "outcome": "success", "asset_type": "image", "delay": "0"},
    )
    return r.json()["job_id"]


SUBMITTERS = {
    "seedance": lambda client, tmp_path, monkeypatch: _submit_seedance(client),
    "kling3": lambda client, tmp_path, monkeypatch: _submit_kling3(client),
    "nanobanana": lambda client, tmp_path, monkeypatch: _submit_nanobanana(client),
    "fake_ai": _submit_fake_ai,
}


@pytest.mark.parametrize("task", ["seedance", "kling3", "nanobanana", "fake_ai"])
def test_every_stored_param_has_a_matching_form_field(
    client, task, tmp_path, monkeypatch
):
    job_id = SUBMITTERS[task](client, tmp_path, monkeypatch)
    params = JOBS[job_id]["params"]

    html = client.get("/generate").text
    field_names = _fieldset_field_names(html, task)
    picker_fields = _fieldset_picker_fields(html, task)

    missing_scalars = [
        key
        for key, value in params.items()
        if key != "prompt" and not isinstance(value, list) and key not in field_names
    ]
    missing_pickers = [
        key
        for key, value in params.items()
        if isinstance(value, list) and key not in picker_fields
    ]
    assert not missing_scalars, (
        f"{task}: params {missing_scalars} have no matching form field -- clicking "
        f"this gallery card would silently fail to restore them"
    )
    assert not missing_pickers, (
        f"{task}: reference params {missing_pickers} have no matching file-picker "
        f"widget -- clicking this gallery card would silently drop those files"
    )


def test_prompt_field_exists_outside_any_fieldset(client):
    html = client.get("/generate").text
    assert '<textarea name="prompt"' in html
    # the prompt reference highlighter needs both the marker class and its script
    assert 'class="prompt-input"' in html
    assert "prompt-highlight.js" in html


def test_gallery_shows_recreate_button_on_done_and_failed_cards(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(fake_ai_router, "OUT_DIR", tmp_path)

    done_id = client.post(
        "/fake-ai/run",
        data={
            "prompt": "ok run",
            "outcome": "success",
            "asset_type": "image",
            "delay": "0",
        },
    ).json()["job_id"]
    failed_id = client.post(
        "/fake-ai/run",
        data={
            "prompt": "bad run",
            "outcome": "error",
            "error_message": "nope",
            "delay": "0",
        },
    ).json()["job_id"]

    html = client.get("/generate").text
    for job_id in (done_id, failed_id):
        card = re.search(rf'data-job-id="{job_id}"(.*?)<p class="source">', html, re.S)
        assert card, f"no gallery card for job {job_id}"
        assert "recreate-btn" in card.group(
            1
        ), f"job {job_id}'s card is missing the Recreate button"


def test_still_running_card_carries_poll_attributes_on_page_reload(client):
    """Regression test: leaving /generate (e.g. to check the Action log)
    while a job is still running and coming back must not leave that job's
    card spinning forever. The server-rendered card for a running job has to
    carry data-pending + data-poll-prefix so generation.js's
    resumePendingCards() picks it back up and finishes the job inline
    instead of requiring a manual refresh."""
    job_id = create_job(
        "fake_ai",
        {
            "prompt": "still going",
            "outcome": "success",
            "asset_type": "image",
            "delay": 30,
            "error_message": "x",
            "reference_images": [],
            "reference_videos": [],
            "reference_audio": [],
        },
    )
    assert JOBS[job_id]["status"] == "running"

    html = client.get("/generate").text
    card = re.search(rf'data-job-id="{job_id}"(.*?)>', html, re.S)
    assert card, f"no gallery card for job {job_id}"
    assert "data-pending" in card.group(1)
    assert 'data-poll-prefix="/fake-ai"' in card.group(1)

    # once it finishes, the pending marker disappears (server already knows)
    JOBS[job_id]["status"] = "done"
    JOBS[job_id]["result"] = [{"name": "x.png", "path": "/tmp/x.png"}]
    html2 = client.get("/generate").text
    card2 = re.search(rf'data-job-id="{job_id}"(.*?)>', html2, re.S)
    assert "data-pending" not in card2.group(1)
