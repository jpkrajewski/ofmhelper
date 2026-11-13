"""The date filter of the gallery pages (/generate, /clone, /download-assets;
owner 2026-11-12): `?date_from=&date_to=` shows only the runs started on those days,
both included, days of the reader's own time zone (cookie `ofm_timezone`).
The page, its scroll pages (`/gallery`) and its live poll (`/gallery/latest`)
all stay inside the filter; one shared control draws it (`ui.date_range`)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, list_jobs_page
from ofmhelpers.schemas.persistence import Created
from ofmhelpers.web.main import app

WARSAW = ZoneInfo("Europe/Warsaw")
# page, a task its gallery shows, whether it has a live poll
PAGES = [
    ("/generate", "ai", True),
    ("/clone", "clone", True),
    ("/download-assets", "download_media", False),
]


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _job_at(client: TestClient, task: str, when: datetime) -> str:
    with time_machine.travel(when, tick=False):
        return create_job(task, {}, actor=str(client.user.id))  # type: ignore[attr-defined]


def _shown(html: str) -> list[str]:
    """The jobs of the gallery's own cards, in order (the page's `<template>`s
    for live cards carry no job id)."""
    return list(dict.fromkeys(re.findall(r'data-job-id="([^"]+)"', html)))


def _three_days(client: TestClient, task: str) -> list[str]:
    """One run on each of 10, 11 and 12 November (Warsaw noon), oldest first."""
    return [
        _job_at(client, task, datetime(2026, 11, day, 12, 0, tzinfo=WARSAW))
        for day in (10, 11, 12)
    ]


@pytest.mark.parametrize(("page", "task", "_live"), PAGES)
def test_a_page_shows_only_the_runs_of_the_days_asked_for(client, page, task, _live):
    old, mid, new = _three_days(client, task)

    assert _shown(client.get(page).text) == [new, mid, old]
    one_day = client.get(
        page, params={"date_from": "2026-11-11", "date_to": "2026-11-11"}
    )
    assert _shown(one_day.text) == [mid]
    assert _shown(client.get(page, params={"date_from": "2026-11-11"}).text) == [
        new,
        mid,
    ]
    assert _shown(client.get(page, params={"date_to": "2026-11-11"}).text) == [mid, old]


@pytest.mark.parametrize(("page", "task", "_live"), PAGES)
def test_dates_the_other_way_round_and_blank_ones_are_taken(client, page, task, _live):
    old, mid, _new = _three_days(client, task)

    swapped = client.get(
        page, params={"date_from": "2026-11-11", "date_to": "2026-11-10"}
    )
    assert _shown(swapped.text) == [mid, old]
    # The form sends an empty date as `date_to=`.
    blank = client.get(page, params={"date_from": "2026-11-12", "date_to": ""})
    assert blank.status_code == 200
    assert len(_shown(blank.text)) == 1
    assert client.get(page, params={"date_from": "yesterday"}).status_code == 422


@pytest.mark.parametrize(("page", "task", "live"), PAGES)
def test_the_scroll_and_the_live_poll_stay_inside_the_filter(
    client, page, task, live, monkeypatch
):
    monkeypatch.setenv("OFM_GALLERY_LIMIT", "1")
    monkeypatch.setenv("OFM_DOWNLOAD_GALLERY_PAGE", "1")
    assert (settings.web.gallery_limit, settings.web.download_gallery_page) == (1, 1)
    old, mid, _new = _three_days(client, task)
    dates = "?date_from=2026-11-10&amp;date_to=2026-11-11"

    first = client.get(
        page, params={"date_from": "2026-11-10", "date_to": "2026-11-11"}
    )

    assert _shown(first.text) == [mid]
    # The links the page's scripts fetch carry the two dates.
    assert f'data-gallery-endpoint="{page}/gallery{dates}"' in first.text
    asked = {"date_from": "2026-11-10", "date_to": "2026-11-11"}
    second = client.get(f"{page}/gallery", params={**asked, "offset": 1})
    assert _shown(second.text) == [old]
    assert "gallery-sentinel" not in second.text
    if live:
        assert f'data-live-endpoint="{page}/gallery/latest{dates}"' in first.text
        latest = client.get(f"{page}/gallery/latest", params=asked)
        assert _shown(latest.text) == [mid]


@pytest.mark.parametrize("asked", ["date_to=9999-12-31", "date_from=0001-01-01"])
def test_a_day_no_calendar_offers_is_refused_not_a_crash(client, asked):
    """Typed into the address bar: the last day there is has no day after it."""
    for page in ("/clone", "/clone/gallery", "/clone/gallery/latest"):
        assert client.get(f"{page}?{asked}").status_code == 422


def test_without_dates_the_links_carry_none(client):
    html = client.get("/clone").text

    assert 'data-gallery-endpoint="/clone/gallery"' in html
    assert 'data-live-endpoint="/clone/gallery/latest"' in html


def test_a_day_is_the_readers_own_day(client):
    """23:30 on the 11th in Warsaw is 04:00 on the 12th in Ambala: the same
    run is the 11th's for one reader and the 12th's for the other."""
    job = _job_at(client, "clone", datetime(2026, 11, 11, 23, 30, tzinfo=WARSAW))
    the_11th = {"date_from": "2026-11-11", "date_to": "2026-11-11"}
    the_12th = {"date_from": "2026-11-12", "date_to": "2026-11-12"}

    assert _shown(client.get("/clone", params=the_11th).text) == [job]
    assert _shown(client.get("/clone", params=the_12th).text) == []

    client.cookies.set("ofm_timezone", "Asia/Kolkata")
    assert _shown(client.get("/clone", params=the_11th).text) == []
    assert _shown(client.get("/clone", params=the_12th).text) == [job]
    assert _shown(client.get("/clone/gallery", params=the_12th).text) == [job]


@pytest.mark.parametrize(("page", "_task", "_live"), PAGES)
def test_the_filter_is_a_from_and_a_to_button_with_the_days_picked(
    client, page, _task, _live
):
    """Nothing to type (owner 2026-11-12): "From" and "To", each a button
    with a calendar, the day picked on it, and Clear back to every day."""
    asked = {"date_from": "2026-11-01", "date_to": "2026-11-12"}
    html = client.get(page, params=asked).text

    head, form = html.split('class="date-range"', 1)
    form = form.split("</form>", 1)[0]
    assert f'action="{page}"' in head[-80:]
    labels = re.findall(r'date-pick-btn">.*?</svg>\s*([^<]+)<', form)
    assert labels == ["From: 01 Nov 2026", "To: 12 Nov 2026"]
    assert 'name="date_from" aria-label="From" tabindex="-1" value="2026-11-01"' in form
    assert 'name="date_to" aria-label="To" tabindex="-1" value="2026-11-12"' in form
    assert f'href="{page}"' in form
    assert "Clear" in form
    assert "js/autosubmit.js" in html
    # No day picked: the bare words, no Clear.
    bare = client.get(page).text.split('class="date-range"', 1)[1].split("</form>")[0]
    found = re.findall(r'date-pick-btn">.*?</svg>\s*([^<]+)<', bare)
    assert found == ["From", "To"]
    assert "Clear" not in bare


def test_a_calendar_ends_today_in_the_readers_zone(client):
    """No day after today can be picked; today is the header clock's day."""
    for zone in ("Europe/Warsaw", "Asia/Kolkata"):
        client.cookies.set("ofm_timezone", zone)
        today = datetime.now(ZoneInfo(zone)).date().isoformat()

        form = client.get("/clone").text.split('class="date-range"', 1)[1]

        assert form.split("</form>", 1)[0].count(f'max="{today}"') == 2


def test_another_persons_runs_stay_out_whatever_the_dates(client):
    other = TestClient(app)
    other.user = sign_in(other, "other@example.com")  # type: ignore[attr-defined]
    _job_at(other, "clone", datetime(2026, 11, 11, 12, 0, tzinfo=WARSAW))

    html = client.get(
        "/clone", params={"date_from": "2026-11-01", "date_to": "2026-11-30"}
    ).text

    assert _shown(html) == []


def test_the_store_filters_the_span_in_sql_and_counts_inside_it(client):
    actor = str(client.user.id)  # type: ignore[attr-defined]
    old, mid, new = _three_days(client, "clone")
    start = datetime(2026, 11, 11, tzinfo=WARSAW).timestamp()
    end = datetime(2026, 11, 12, tzinfo=WARSAW).timestamp()

    inside = list_jobs_page(
        {"clone"}, 0, 10, actor, created=Created(since=start, until=end)
    )
    since = list_jobs_page({"clone"}, 0, 1, actor, created=Created(since=start))

    assert ([j.id for j in inside.jobs], inside.total) == ([mid], 1)
    # The total is the filter's, not the history's: what ends the scroll.
    assert ([j.id for j in since.jobs], since.total) == ([new], 2)
    # `until` is not included: a run at that very moment is the next day's.
    edge = list_jobs_page({"clone"}, 0, 10, actor, created=Created(until=start))
    assert [j.id for j in edge.jobs] == [old]


def test_clones_own_from_parameter_is_left_alone(client):
    """/clone's `?from=` is where "Clone it" on /edit came from, not a date:
    the page opens with it (the first e2e run of this filter answered 422)."""
    r = client.get("/clone", params={"from": "/edit?video=x"})

    assert r.status_code == 200
    assert 'data-gallery-endpoint="/clone/gallery"' in r.text
