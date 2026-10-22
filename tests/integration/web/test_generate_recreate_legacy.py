"""
Recreate on a job of a per-model tool /ai replaced (task `seedream5`,
`wan3`, ...) restored only the prompt: those params name no `provider`/`model`,
so ai-form.js fell back to prompt-only, and the next run went to whatever model
was picked, with no references -- a different picture. Their cards now carry
the params as the /ai job they would be today: a catalog model, and inputs
under that model's schema names, so Recreate restores all of it.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import html
import json
import re

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.web.main import app
from ofmhelpers.web.stores.jobs import create_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

REFS = ["uploads/assets/a__one.png", "/app/uploads/assets/b__two.png"]


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _finished(task: str, params: dict, tmp_path) -> str:
    out = tmp_path / f"{task}.png"
    out.write_bytes(b"png")
    job_id = create_job(task, params)
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})
    return job_id


def _card_params(page: str, job_id: str) -> dict:
    m = re.search(rf'data-job-id="{job_id}"[^>]*?data-params=\'([^\']*)\'', page)
    assert m, f"no card for {job_id}"
    return json.loads(html.unescape(m.group(1)))


def _assert_fits_its_model(params: dict) -> None:
    """Every input is a field of the model's schema, so ai-form.js finds a
    field or picker for each."""
    spec = get_spec(params["model"])
    assert params["provider"] == spec.provider
    inputs = set(params) - {"provider", "model"}
    assert inputs <= set(spec.properties), inputs - set(spec.properties)


def test_seedream5_card_restores_model_settings_and_references(client, tmp_path):
    job_id = _finished(
        "seedream5",
        {
            "prompt": "guy from first image",
            "quality": "high",
            "variant": "pro",
            "image_urls": REFS,
            "aspect_ratio": "9:16",
            "output_format": "png",
        },
        tmp_path,
    )

    params = _card_params(client.get("/generate").text, job_id)

    assert params["model"] == "kie:seedream/5-pro-image-to-image"
    assert params["image_urls"] == REFS
    assert params["quality"] == "high"
    assert params["aspect_ratio"] == "9:16"
    assert "variant" not in params
    _assert_fits_its_model(params)


def test_seedream5_without_references_is_text_to_image(client, tmp_path):
    job_id = _finished(
        "seedream5",
        {"prompt": "p", "variant": "lite", "image_urls": [], "quality": "basic"},
        tmp_path,
    )

    params = _card_params(client.get("/generate").text, job_id)

    assert params["model"] == "kie:seedream/5-lite-text-to-image"
    assert "image_urls" in params  # empty list: clears the picker, harmless


def test_wan3_references_move_to_the_schema_names(client, tmp_path):
    job_id = _finished(
        "wan3",
        {
            "audio": True,
            "prompt": "replace the subject",
            "duration": 7,
            "resolution": "720P",
            "aspect_ratio": "adaptive",
            "reference_audio": [],
            "reference_images": REFS[:1],
            "reference_videos": ["uploads/assets/c__clip.mp4"],
        },
        tmp_path,
    )

    params = _card_params(client.get("/generate").text, job_id)

    assert params["model"] == "kie:wan/3-0-video"
    assert params["reference_image_urls"] == REFS[:1]
    assert params["reference_video_urls"] == ["uploads/assets/c__clip.mp4"]
    assert params["reference_audio_urls"] == []
    _assert_fits_its_model(params)


@pytest.mark.parametrize(
    ("task", "params", "model"),
    [
        (
            "nanobanana",
            {
                "prompt": "p",
                "resolution": "2K",
                "image_input": REFS,
                "aspect_ratio": "1:1",
                "output_format": "png",
            },
            "kie:nano-banana-pro",
        ),
        (
            "gpt_image",
            {
                "prompt": "p",
                "input_urls": REFS,
                "aspect_ratio": "1:1",
                "background": "auto",
                "resolution": "1K",
            },
            "kie:gpt-image-2-5-flare-image-to-image",
        ),
    ],
)
def test_same_named_tools_gain_their_model(client, tmp_path, task, params, model):
    job_id = _finished(task, params, tmp_path)

    card = _card_params(client.get("/generate").text, job_id)

    assert card == {"provider": "kie", "model": model, **params}
    _assert_fits_its_model(card)


def test_ai_and_untranslated_jobs_keep_their_params(client, tmp_path):
    ai_params = {"provider": "kie", "model": "kie:nano-banana-pro", "prompt": "p"}
    ai_job = _finished("ai", ai_params, tmp_path)
    fake_job = _finished("fake_ai", {"prompt": "q"}, tmp_path)

    page = client.get("/generate").text

    assert _card_params(page, ai_job) == ai_params
    assert _card_params(page, fake_job) == {"prompt": "q"}
