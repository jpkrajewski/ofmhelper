"""
/download-assets renders only `download_gallery_page` cards (prod: a page of
20 media cards was slow to open, 2026-10-22); gallery-scroll.js fetches the
rest from GET /download-assets/gallery?offset=N. Pages don't overlap, every
job is reachable, and the sentinel is gone after the last page.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.stores.jobs import create_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

PAGE = 6


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def jobs(tmp_path):
    """Two full pages and one more, oldest first."""
    ids = []
    for i in range(PAGE * 2 + 1):
        out = tmp_path / f"img{i}.png"
        out.write_bytes(b"png")
        job_id = create_job("download_images", {"urls": [f"https://x/{i}"]})
        run_job(job_id, lambda p=out: [{"name": p.name, "path": str(p)}], {})
        ids.append(job_id)
    return ids


def _ids(html: str) -> list[str]:
    return re.findall(r'data-job-id="([^"]+)"', html)


def _next(html: str) -> str | None:
    found = re.search(r'gallery-sentinel" data-next-offset="(\d+)"', html)
    return found.group(1) if found else None


def test_page_renders_only_the_first_page_and_a_sentinel(client, jobs):
    html = client.get("/download-assets").text

    assert _ids(html) == list(reversed(jobs))[:PAGE]
    assert _next(html) == str(PAGE)
    assert 'data-gallery-endpoint="/download-assets/gallery"' in html
    assert "js/gallery-scroll.js" in html


def test_scrolling_reaches_every_job_once_then_stops(client, jobs):
    html = client.get("/download-assets").text
    seen = _ids(html)
    offset = _next(html)
    while offset is not None:
        fragment = client.get(f"/download-assets/gallery?offset={offset}").text
        seen += _ids(fragment)
        offset = _next(fragment)

    assert seen == list(reversed(jobs))


def test_fragment_cards_match_the_page_cards(client, jobs):
    """Same partial: a card appended on scroll is the card the page draws."""
    page = client.get("/download-assets").text
    fragment = client.get("/download-assets/gallery?offset=0").text
    card = re.compile(r'<div class="result-item".*?</p>\s*</div>', re.DOTALL)
    assert card.findall(fragment) == card.findall(page)[: len(card.findall(fragment))]
