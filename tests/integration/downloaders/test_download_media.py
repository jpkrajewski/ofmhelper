# Copyright (C) 2026 jpkrajewski

import pytest
from fastapi.testclient import TestClient
from idempotent import keyed
from signin import sign_in

from ofmhelpers.features.fakes import install_fakes
from ofmhelpers.integrations.downloaders import generic, images, kvs
from ofmhelpers.web.main import app
lazy from ofmhelpers.integrations.downloaders.models import DownloadResult


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))
    install_fakes()
    c = TestClient(app)
    sign_in(c)
    return c


def test_one_run_gets_a_reels_video_and_a_posts_images(client):
    reel = "https://www.instagram.com/reel/abc/"
    post = "https://www.instagram.com/p/xyz/"

    r = client.post(
        "/download-media/run",
        data={"urls": f"{reel}\n{post}"},
        headers=keyed(),
    )
    assert r.status_code == 202
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    assert status["status"] == "done"
    kinds = [(a["source"], a["kind"]) for a in status["result"]]
    assert kinds == [(reel, "video"), (post, "image"), (post, "image")]
    assert status["failed_sources"] == []
    for asset in status["result"]:
        assert client.get(asset["view_url"]).status_code == 200


def test_a_dead_link_reports_both_downloaders(client):
    r = client.post(
        "/download-media/run",
        data={"urls": "https://www.tiktok.com/fake-fail"},
        headers=keyed(),
    )
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    (failed,) = status["failed_sources"]
    assert failed["error"].startswith("video: ERROR: Video unavailable")
    assert "images: gallery-dl: 404 Not Found" in failed["error"]


def test_links_pasted_on_one_line_with_spaces_all_download(client):
    first = "https://www.instagram.com/reel/abc/"
    second = "https://www.instagram.com/reel/def/"
    third = "https://www.instagram.com/reel/ghi/"

    r = client.post(
        "/download-media/run",
        data={"urls": f"{first} {second}\n{third}"},
        headers=keyed(),
    )
    assert r.status_code == 202
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    assert [a["source"] for a in status["result"]] == [first, second, third]


def test_text_that_is_not_a_link_is_refused(client):
    refused = client.post(
        "/download-media/run",
        data={"urls": "https://www.instagram.com/reel/abc/ see this"},
        headers=keyed(),
    )
    assert refused.status_code == 400
    assert "separated by spaces or new lines" in refused.text


def test_an_empty_form_is_refused(client):
    refused = client.post("/download-media/run", data={"urls": " \n "}, headers=keyed())
    assert refused.status_code == 400


def test_a_download_run_shows_on_the_page_and_reopens_in_the_one_tool(client):
    r = client.post(
        "/download-media/run",
        data={"urls": "https://www.instagram.com/reel/abc/"},
        headers=keyed(),
    )
    html = client.get("/download-assets").text

    assert f'data-job-id="{r.json()["job_id"]}"' in html
    assert 'data-task="download_media"' in html
    assert 'data-tasks="download_media download_videos download_images"' in html


def test_a_login_walled_reel_is_rescued_by_the_saver_fallback(client, monkeypatch):
    walled = "content isn't available to everyone"
    monkeypatch.setattr(
        generic,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url,
            success=False,
            error=walled,
        ),
    )
    monkeypatch.setattr(
        images,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url,
            success=False,
            error="This post needs a login",
        ),
    )
    monkeypatch.setattr(
        kvs,
        "download",
        lambda url, output_dir=None: DownloadResult(
            url=url,
            success=False,
            error="Unsupported URL: no KVS player",
        ),
    )

    url = "https://www.instagram.com/reel/rescued/"
    r = client.post("/download-media/run", data={"urls": url}, headers=keyed())
    status = client.get(f"/download-media/jobs/{r.json()['job_id']}/status").json()

    assert status["status"] == "done"
    assert status["failed_sources"] == []
    kinds = [(a["source"], a["kind"]) for a in status["result"]]
    assert kinds == [(url, "video")]
