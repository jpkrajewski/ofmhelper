"""
Covers the editable default-prompt helpers on /generate:
- Kling 3.0's prompt textarea should default to a handheld-camera-style
  template (issue: users had to write this boilerplate by hand every time).
- Nano Banana Pro's prompt textarea should always default to reference-by-
  position language -- unconditionally, no reference-image-count gate.
Both are plain JS behaviors wired into generate_form.html -- this repo has no
JS test runner, so these just confirm the template actually ships the
expected default text and wiring (a regression here means the feature
silently stopped being served to the browser at all).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_kling_default_prompt_template_is_shipped(client):
    html = client.get("/generate").text
    assert "Handheld camera movement, slight natural shake" in html
    assert "no cinematic stabilization" in html
    assert "applyPromptDefault" in html


def test_nanobanana_multi_ref_default_prompt_is_shipped_unconditionally(client):
    """No reference-image-count gate -- the default is set the moment the
    Nanobanana panel is opened, not only once 2+ images are attached."""
    html = client.get("/generate").text
    assert "Refer to first image and second image, replicate third image." in html
    assert "_items.length" not in html
    assert "filepicker:change" not in html


def test_prompt_defaults_never_overwrite_a_real_edit(client):
    """The default is a helper, not a locked value -- applyPromptDefault must
    bail out whenever the textarea holds something other than empty or a
    default it previously set itself."""
    html = client.get("/generate").text
    assert (
        "if (promptEl.value !== '' && promptEl.value !== lastAppliedPromptDefault) return;"
        in html
    )


def test_switching_tools_replaces_an_untouched_default_instead_of_leaving_it(client):
    """Regression: Kling's default text must not linger in the shared prompt
    textarea after switching to Nanobanana (or any other tool) -- only a
    genuine user edit should survive a tool switch, not a still-untouched
    default from whichever tool was selected before."""
    html = client.get("/generate").text
    assert "let lastAppliedPromptDefault = '';" in html
    assert "lastAppliedPromptDefault = next;" in html


def test_no_stale_paste_hint_text(client):
    """Paste-to-upload works globally (see file-picker.js's paste listener)
    -- there must be no leftover "click here first" instruction telling
    users to do something the feature doesn't actually require."""
    html = client.get("/generate").text
    assert "Click here" not in html
