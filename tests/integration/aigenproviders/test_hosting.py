"""This server as the file host of a provider without uploads
(`ai_providers/hosting.py` + `GET /files/ai/<token>/<name>`): a link serves
exactly its file to anyone holding it, no session needed; an unknown,
malformed or expired token is a 404; no public https address = no link."""

import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.cache import delete_text
from ofmhelpers.integrations.ai_providers import hosting
from ofmhelpers.integrations.ai_providers.errors import ProviderInputError
from ofmhelpers.web.main import app

BASE = "https://ascendglobal.test"


@pytest.fixture(autouse=True)
def public_server(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", f"{BASE}/")
    monkeypatch.delenv("OFM_AIGEN_PUBLIC_BASE_URL", raising=False)


@pytest.fixture
def clip(tmp_path) -> Path:
    path = tmp_path / "my clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40)
    return path


def _path(url: str) -> str:
    return url.removeprefix(BASE)


def test_a_link_serves_its_file_to_anyone_without_signing_in(clip):
    url = hosting.link(clip, "APIYI", 3600)

    response = TestClient(app).get(_path(url))

    assert url.startswith(f"{BASE}/files/ai/")
    # Never the file's own name in a public link, only its type.
    assert url.endswith("/ref.mp4")
    assert response.status_code == 200
    assert response.content == clip.read_bytes()
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


def test_a_head_answers_without_the_body(clip):
    url = hosting.link(clip, "APIYI", 3600)

    response = TestClient(app).head(_path(url))

    assert response.status_code == 200
    assert response.headers["content-length"] == str(clip.stat().st_size)
    assert response.content == b""


def test_a_range_is_served(clip):
    url = hosting.link(clip, "APIYI", 3600)

    response = TestClient(app).get(_path(url), headers={"Range": "bytes=0-15"})

    assert response.status_code == 206
    assert response.content == clip.read_bytes()[:16]


@pytest.mark.parametrize("token", ["x" * 43, "a" * 65, "%2e%2e", "a.b", "%00"])
def test_a_token_we_never_gave_is_a_404(token):
    response = TestClient(app).get(f"/files/ai/{token}/clip.mp4")

    assert response.status_code == 404


def test_an_expired_link_is_a_404(clip):
    url = hosting.link(clip, "APIYI", 3600)
    token = _path(url).split("/")[3]
    delete_text(f"aigen:hosted:{token}")

    assert TestClient(app).get(_path(url)).status_code == 404


def test_a_link_to_a_file_deleted_since_is_a_404(clip):
    url = hosting.link(clip, "APIYI", 3600)
    clip.unlink()

    assert TestClient(app).get(_path(url)).status_code == 404


def test_one_file_keeps_one_link_and_a_dead_one_is_replaced(clip):
    first = hosting.link(clip, "APIYI", 3600)
    assert hosting.link(clip, "APIYI", 3600) == first

    delete_text(f"aigen:hosted:{_path(first).split('/')[3]}")
    second = hosting.link(clip, "APIYI", 3600)

    assert second != first
    assert TestClient(app).get(_path(second)).status_code == 200


def test_two_files_get_two_links(clip, tmp_path):
    other = tmp_path / "other.mp4"
    other.write_bytes(b"other")

    assert hosting.link(clip, "APIYI", 3600) != hosting.link(other, "APIYI", 3600)


def test_without_a_public_https_address_there_is_no_link(monkeypatch, clip):
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000/")

    with pytest.raises(ProviderInputError, match="APIYI fetches my clip.mp4"):
        hosting.link(clip, "APIYI", 3600)


def test_a_new_public_address_never_gets_the_old_addresss_link(monkeypatch, clip):
    """Dev tunnels change address on every start: a link remembered for the
    old one would send the provider to a host that is gone."""
    monkeypatch.setenv("OFM_AIGEN_PUBLIC_BASE_URL", "https://one.example/")
    old = hosting.link(clip, "APIYI", 3600)
    monkeypatch.setenv("OFM_AIGEN_PUBLIC_BASE_URL", "https://two.example/")

    assert hosting.link(clip, "APIYI", 3600).startswith("https://two.example/")
    assert old.startswith("https://one.example/")


def test_the_public_base_setting_wins_over_app_base_url(monkeypatch, clip):
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000/")
    monkeypatch.setenv("OFM_AIGEN_PUBLIC_BASE_URL", "https://tunnel.example/")

    assert hosting.link(clip, "APIYI", 3600).startswith(
        "https://tunnel.example/files/ai/"
    )
