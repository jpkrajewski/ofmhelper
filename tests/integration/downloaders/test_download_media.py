"""/download-media end to end on the fake downloaders (OFM_FAKE_EXTERNALS):
a reel comes back as its video, a photo post as its images, in one run,
without anyone picking a tool."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))
    c = TestClient(app)
    sign_in(c)
    return c


def test_one_run_gets_a_reels_video_and_a_posts_images(client):
    reel = "https://www.instagram.com/reel/abc/"
    post = "https://www.instagram.com/p/xyz/"

    r = client.post("/download-media/run", data={"urls": f"{reel}\n{post}"})
    assert r.status_code == 200
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    assert status["status"] == "done"
    kinds = [(a["source"], a["kind"]) for a in status["result"]]
    assert kinds == [(reel, "video"), (post, "image"), (post, "image")]
    assert status["failed_sources"] == []
    for asset in status["result"]:
        assert client.get(asset["view_url"]).status_code == 200


def test_a_dead_link_reports_both_downloaders(client):
    r = client.post(
        "/download-media/run", data={"urls": "https://www.tiktok.com/fake-fail"}
    )
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    (failed,) = status["failed_sources"]
    assert failed["error"].startswith("video: ERROR: Video unavailable")
    assert "images: gallery-dl: 404 Not Found" in failed["error"]


def test_an_empty_form_is_refused(client):
    assert client.post("/download-media/run", data={"urls": " \n "}).status_code == 400


def test_a_download_run_shows_on_the_page_and_reopens_in_the_one_tool(client):
    r = client.post(
        "/download-media/run", data={"urls": "https://www.instagram.com/reel/abc/"}
    )
    html = client.get("/download-assets").text

    assert f'data-job-id="{r.json()["job_id"]}"' in html
    assert 'data-task="download_media"' in html
    # Old runs of the two tools it replaced map onto it for Recreate.
    assert 'data-tasks="download_media download_videos download_images"' in html


def test_a_login_walled_reel_is_rescued_by_the_saver_fallback(client, monkeypatch):
    """Prod 2026-10-28: every downloader refused a reel Instagram hides
    from the logged-out API; the saver fallback fetches it."""
    from ofmhelpers.downloaders import generic, images, kvs
    from ofmhelpers.downloaders.models import DownloadResult

    walled = "content isn't available to everyone"
    monkeypatch.setattr(
        generic,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=False, error=walled
        ),
    )
    monkeypatch.setattr(
        images,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=False, error="This post needs a login"
        ),
    )
    monkeypatch.setattr(
        kvs,
        "download",
        lambda url, output_dir=None: DownloadResult(
            url=url, success=False, error="Unsupported URL: no KVS player"
        ),
    )

    url = "https://www.instagram.com/reel/rescued/"
    r = client.post("/download-media/run", data={"urls": url})
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    assert status["status"] == "done"
    assert status["failed_sources"] == []
    kinds = [(a["source"], a["kind"]) for a in status["result"]]
    assert kinds == [(url, "video")]
