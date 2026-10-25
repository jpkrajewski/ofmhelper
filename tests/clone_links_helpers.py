"""Shared by the /clone-links integration tests (one file per part: the
run, the previews, the reviews): every outside service faked, a signed-in
client with Google Drive connected, and a Generate posted the way the page
posts it. Jobs run inline (the test suite's queue)."""

import json

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_KEY
from ofmhelpers.downloaders import media as downloader
from ofmhelpers.gdrive.fake import fake_drive
from ofmhelpers.llm.gemini.fake import FAKE_KEY as GEMINI_KEY
from ofmhelpers.utils import fake_media
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.stores import users
from ofmhelpers.web.stores.users import _repository as user_repository

NEW = json.dumps([{"kind": "new"}])
REEL = "https://www.instagram.com/reel/ABC123/"
OTHER = "https://www.instagram.com/reel/XYZ789/"
GLITCHY = {
    "reasoning": "At 0:03.0 the face melts into the wall.",
    "glitches": [{"at_s": 3.0, "what": "face melting", "severity": "blocking"}],
    "glitch_percent": 92,
}
GOOD = {"reasoning": "Nothing looks off.", "glitches": [], "glitch_percent": 3}


@pytest.fixture
def clone_links_env(fake_externals, monkeypatch, tmp_path):
    """Every outside service faked: downloads, Wan 3.0 (kie.ai), Gemini,
    Drive. Retries off and one model in the AI check's chain: a failure is
    the answer. Returns the fake Drive."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")
    monkeypatch.setenv("LLM_FALLBACK_VIDEO", "")
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path / "assets")
    monkeypatch.setattr(downloader, "_OUTPUT_ROOT", tmp_path / "downloads")
    return fake_drive()


def client(email: str = "ann@example.com", *, google: bool = True) -> TestClient:
    """A signed-in session; `google`: with Google Drive connected."""
    c = TestClient(app)
    user = sign_in(c, email)
    if google:
        user_repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return c


def post_run(c: TestClient, links: list[str], **data):
    """POST /clone-links/run as the page sends it (kie.ai, one new subject
    image, Original export); the raw response."""
    form = {
        "api_key": KIE_KEY,
        "provider": "kie",
        "image_manifest": NEW,
        "export": "original",
        "links": links,
        **data,
    }
    files = [("image", ("face.png", fake_media.image("salmon"), "image/png"))]
    return c.post("/clone-links/run", data=form, files=files)


def run(c: TestClient, links: list[str], **data) -> dict:
    r = post_run(c, links, **data)
    assert r.status_code == 200, r.text
    return r.json()


def root(email: str = "ann@example.com") -> str:
    found = users.by_email(email).drive_root_id
    assert found, f"{email} has no Drive root"
    return found


def files_in(drive, folder: str, email: str = "ann@example.com") -> list[str]:
    (folder_id,) = drive.named(folder, root(email))
    return drive.names_in(folder_id)
