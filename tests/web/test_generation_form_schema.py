"""`ReferenceUploads` is the one form shape the generation routers share.

The thing worth pinning is that it is genuinely *shared*: the same multipart
body has to land the same way on two different tools, and an empty picker has
to arrive as `[]` rather than the `None` Starlette actually sends.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import ReferenceUploads
from ofmhelpers.web.stores.jobs import get_job


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.post("/login", data={"password": "test-admin", "next": "/"})
        yield c


@pytest.fixture(autouse=True)
def _isolated_assets(monkeypatch, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", assets)
    return assets


def test_empty_pickers_arrive_as_lists_not_none():
    """Starlette hands a missing file field through as None; every router used
    to open with three `if x is None` lines to undo that."""
    refs = ReferenceUploads.from_form()
    assert (refs.images, refs.videos, refs.audio) == ([], [], [])
    assert refs.images_manifest == "[]"


def test_the_same_body_lands_identically_on_seedance_and_fake_ai(client):
    def body():
        return {
            "reference_images": (
                "a.png",
                io.BytesIO(b"\x89PNG\r\n\x1a\nA"),
                "image/png",
            ),
        }

    def form(extra):
        return {
            "prompt": "p",
            "reference_images_manifest": '[{"kind": "new"}]',
            **extra,
        }

    seedance = client.post(
        "/seedance/run", data=form({"api_key": "k"}), files=body()
    ).json()["job_id"]
    fake = client.post("/fake-ai/run", data=form({}), files=body()).json()["job_id"]

    seedance_refs = get_job(seedance)["params"]["reference_images"]
    fake_refs = get_job(fake)["params"]["reference_images"]
    assert len(seedance_refs) == 1
    # Same bytes, same content-addressed asset -- and both routers key the
    # stored param by the picker's field name, which is what click-to-reuse
    # reads back.
    assert seedance_refs == fake_refs


def test_a_malformed_manifest_falls_back_to_treating_every_upload_as_new(client):
    """A broken manifest must not lose the files the user actually sent -- the
    picker is JS-built, so a bug there would otherwise silently drop uploads."""
    job_id = client.post(
        "/fake-ai/run",
        data={"prompt": "p", "reference_images_manifest": "not json"},
        files={
            "reference_images": (
                "a.png",
                io.BytesIO(b"\x89PNG\r\n\x1a\nA"),
                "image/png",
            )
        },
    ).json()["job_id"]
    assert len(get_job(job_id)["params"]["reference_images"]) == 1
