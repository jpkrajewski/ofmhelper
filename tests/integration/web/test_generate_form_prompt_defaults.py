# Copyright (C) 2026 jpkrajewski

import json
import re

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
    return c


@pytest.fixture
def script(client):
    return client.get("/static/js/ai-form.js").text


def _models(client):
    html = client.get("/generate").text
    raw = re.search(
        r'id="model-catalog" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    return {m["key"]: m for m in json.loads(raw.group(1))}


def test_kling_default_prompt_template_is_shipped(client):
    prompt = _models(client)["kie:kling-3.0/video"]["default_prompt"]

    assert prompt.startswith("Handheld camera movement, slight natural shake")
    assert prompt.endswith("no cinematic stabilization")


def test_nanobanana_multi_ref_default_prompt_is_shipped_unconditionally(client, script):
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
    assert "Click here" not in client.get("/generate").text


def test_switching_models_carries_picked_files_over(script):
    carry = script.index("window.FilePicker.carryOver(fieldset, next)")
    swap = script.index("fieldset.replaceWith(next)")
    assert carry < swap
