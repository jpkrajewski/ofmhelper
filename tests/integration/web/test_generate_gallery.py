import re

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")

PAGE = 3


@pytest.fixture(autouse=True)
def small_pages(monkeypatch):
    monkeypatch.setenv("OFM_GALLERY_LIMIT", str(PAGE))


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
    return c


@pytest.fixture
def gallery_jobs(tmp_path):
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
    assert 'data-gallery-endpoint="/generate/gallery"' in html


def test_the_next_page_continues_where_the_first_stopped(client, gallery_jobs):
    first = _card_ids(client.get("/generate").text)
    second_html = client.get(f"/generate/gallery?offset={PAGE}").text
    second = _card_ids(second_html)

    assert len(second) == PAGE
    assert not set(first) & set(second)
    assert _sentinel_offset(second_html) == str(PAGE * 2)


def test_the_last_page_carries_no_sentinel(client, gallery_jobs):
    html = client.get(f"/generate/gallery?offset={PAGE * 2}").text
    assert len(_card_ids(html)) == 1
    assert _sentinel_offset(html) is None


def test_an_offset_past_the_end_returns_nothing_at_all(client, gallery_jobs):
    html = client.get("/generate/gallery?offset=100000").text
    assert _card_ids(html) == []
    assert _sentinel_offset(html) is None


def test_a_negative_offset_is_rejected(client):
    assert client.get("/generate/gallery?offset=-1").status_code == 422


def test_the_fragment_renders_the_same_card_markup_as_the_page(client, gallery_jobs):
    fragment = client.get(f"/generate/gallery?offset={PAGE}").text
    for attr in ("data-job-id=", "data-task=", "data-params=", "recreate-btn"):
        assert attr in fragment


def test_gallery_cards_show_the_asset_filename(client, tmp_path):
    out = tmp_path / "her-voice.mp3"
    out.write_bytes(b"id3")
    job_id = create_job("fake_ai", {"prompt": "speak"})
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})

    html = client.get("/generate").text
    assert f'data-job-id="{job_id}"' in html
    assert '<p class="filename">her-voice.mp3</p>' in html


def test_gallery_limit_still_comes_from_config(monkeypatch):
    monkeypatch.delenv("OFM_GALLERY_LIMIT")
    assert settings.web.gallery_limit == 20


def test_latest_is_the_newest_page_without_a_sentinel(client, gallery_jobs):
    html = client.get("/generate/gallery/latest").text
    assert _card_ids(html) == _card_ids(client.get("/generate").text)
    assert gallery_jobs[-1] in _card_ids(html)
    assert _sentinel_offset(html) is None
    assert (
        'data-live-endpoint="/generate/gallery/latest"' in client.get("/generate").text
    )
