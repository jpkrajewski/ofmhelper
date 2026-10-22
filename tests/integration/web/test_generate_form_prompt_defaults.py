"""
Covers the editable default prompts and the model switch on /generate:
- Kling 3.0 (kie.ai) defaults to a handheld-camera template and Nano Banana
  Pro to reference-by-position language -- now catalog data
  (`default_prompt`), applied by static/js/ai-form.js.
- A default only ever replaces empty text or a default it put there itself.
- Picked files follow a model switch.
This repo has no JS test runner, so these confirm the page and script ship
the expected text and wiring (a regression here means the feature silently
stopped being served to the browser at all).
"""

import json
import os
import re

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def script(client):
    return client.get("/static/js/ai-form.js").text


def _models(client):
    html = client.get("/generate").text
    raw = re.search(
        r'id="model-catalog" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    return {m["key"]: m for m in json.loads(raw.group(1))}


def test_kling_default_prompt_template_is_shipped(client):
    prompt = _models(client)["kie:kling-3.0/video"]["default_prompt"]

    assert prompt.startswith("Handheld camera movement, slight natural shake")
    assert prompt.endswith("no cinematic stabilization")


def test_nanobanana_multi_ref_default_prompt_is_shipped_unconditionally(client, script):
    """No reference-image-count gate: the default is set the moment the model
    is picked, not only once 2+ images are attached."""
    prompt = _models(client)["kie:nano-banana-pro"]["default_prompt"]

    assert prompt == "Refer to first image and second image, replicate third image."
    assert "_items.length" not in script
    assert "filepicker:change" not in script


def test_prompt_defaults_never_overwrite_a_real_edit(script):
    assert (
        'if (promptBox.value !== "" && promptBox.value !== lastPromptDefault) return;'
        in script
    )


def test_switching_models_replaces_an_untouched_default_instead_of_leaving_it(script):
    assert 'let lastPromptDefault = "";' in script
    assert "lastPromptDefault = next;" in script


def test_no_stale_paste_hint_text(client):
    """Paste-to-upload works globally (see file-picker.js's paste listener)
    -- there must be no leftover "click here first" instruction."""
    assert "Click here" not in client.get("/generate").text


def test_switching_models_carries_picked_files_over(script):
    """The new model's pickers get the old one's files (by kind) before the
    old fieldset leaves the page."""
    carry = script.index("window.FilePicker.carryOver(fieldset, next)")
    swap = script.index("fieldset.replaceWith(next)")
    assert carry < swap
