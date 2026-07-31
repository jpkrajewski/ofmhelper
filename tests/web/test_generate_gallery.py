"""
The /generate gallery used to be a hard 20-item slice with nothing older
reachable. It now pages: the page renders the first page plus a sentinel, and
GET /generate/gallery?offset=N returns the next page as a fragment for
static/js/gallery-scroll.js to append. What matters is that the two render the
same card markup, that pages don't overlap, and that the sentinel disappears at
the end -- its absence is the only thing that stops the scroll.

Page size is patched down to PAGE here rather than fixtured up to the real
gallery_limit: conftest truncates the jobs table before every test, so each one
would otherwise pay for ~40 job inserts to prove arithmetic that three per page
demonstrates just as well. test_gallery_limit_still_comes_from_config keeps the
real default honest.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.config import settings
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.generation import index as gallery_index
from ofmhelpers.web.stores.jobs import create_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

PAGE = 3


@pytest.fixture(autouse=True)
def small_pages(monkeypatch):
    monkeypatch.setattr(gallery_index, "GALLERY_LIMIT", PAGE)


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def gallery_jobs(tmp_path):
    """Enough finished fake_ai jobs for three pages, oldest first."""
    ids = []
    for i in range(PAGE * 2 + 1):
        out = tmp_path / f"asset{i}.png"
        out.write_bytes(b"png")
        job_id = create_job("fake_ai", {"prompt": f"p{i}"})
        run_job(job_id, lambda p=out: [{"name": p.name, "path": str(p)}], {})
        ids.append(job_id)
    return ids


def _card_ids(html: str) -> list[str]:
    return re.findall(r'class="result-item"[^>]*data-job-id="([^"]+)"', html)


def _sentinel_offset(html: str) -> str | None:
    m = re.search(r'class="gallery-sentinel" data-next-offset="(\d+)"', html)
    return m.group(1) if m else None


def test_first_page_is_one_page_plus_a_sentinel(client, gallery_jobs):
    html = client.get("/generate").text
    assert len(_card_ids(html)) == PAGE
    assert _sentinel_offset(html) == str(PAGE)
    # The endpoint gallery-scroll.js reads off the container.
    assert 'data-gallery-endpoint="/generate/gallery"' in html


def test_the_next_page_continues_where_the_first_stopped(client, gallery_jobs):
    first = _card_ids(client.get("/generate").text)
    second_html = client.get(f"/generate/gallery?offset={PAGE}").text
    second = _card_ids(second_html)

    assert len(second) == PAGE
    assert not set(first) & set(second)  # no card served twice
    assert _sentinel_offset(second_html) == str(PAGE * 2)


def test_the_last_page_carries_no_sentinel(client, gallery_jobs):
    """The absence of a sentinel is what ends the scroll -- there is no
    has_more flag that could disagree with the cards actually returned."""
    html = client.get(f"/generate/gallery?offset={PAGE * 2}").text
    assert len(_card_ids(html)) == 1  # the odd one out
    assert _sentinel_offset(html) is None


def test_an_offset_past_the_end_returns_nothing_at_all(client, gallery_jobs):
    html = client.get("/generate/gallery?offset=100000").text
    assert _card_ids(html) == []
    assert _sentinel_offset(html) is None


def test_a_negative_offset_is_rejected(client):
    assert client.get("/generate/gallery?offset=-1").status_code == 422


def test_the_fragment_renders_the_same_card_markup_as_the_page(client, gallery_jobs):
    """Both sides include _generate_gallery_card.html, so an appended card has
    to be indistinguishable from a server-rendered one -- the Recreate handler
    and the resumed poller both key off these attributes."""
    fragment = client.get(f"/generate/gallery?offset={PAGE}").text
    for attr in ("data-job-id=", "data-task=", "data-params=", "recreate-btn"):
        assert attr in fragment


def test_gallery_cards_show_the_asset_filename(client, tmp_path):
    """An <audio> element renders nothing identifying, so a voice result was
    indistinguishable from every other one."""
    out = tmp_path / "her-voice.mp3"
    out.write_bytes(b"id3")
    job_id = create_job("fake_ai", {"prompt": "speak"})
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})

    html = client.get("/generate").text
    assert f'data-job-id="{job_id}"' in html
    assert '<p class="filename">her-voice.mp3</p>' in html


def test_gallery_limit_still_comes_from_config(small_pages):
    """small_pages patched the module constant, not the setting behind it."""
    assert settings.web.gallery_limit == 20
