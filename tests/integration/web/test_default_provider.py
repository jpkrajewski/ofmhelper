"""Which provider a generation page starts on (features/generate/providers.py):
the one the person last ran there (the clone pages share it), if the page
offers it; else OFM_DEFAULT_PROVIDER; else the page's first."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

import re

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.web.main import app

CLONE_PAGES = ["/clone", "/clone-long"]


@pytest.fixture
def client():
    with TestClient(app) as c:
        sign_in(c)
        yield c


@pytest.fixture
def actor(client) -> str:
    return str(sign_in(client).id)


SLOW = "APIYI can be slow right now. Fastest from {17:00Z} to {11:00Z}, {zone} time"


@pytest.fixture
def slow_hours(monkeypatch):
    """The clock inside APIYI's slow hours (15:18 UTC, as on prod
    2026-11-12)."""
    from datetime import UTC, datetime

    from ofmhelpers.integrations.ai_providers.apiyi import provider

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            del tz
            return cls(2026, 11, 12, 15, 18, tzinfo=UTC)

    monkeypatch.setattr(provider, "datetime", Clock)


@pytest.mark.parametrize("page", ["/clone", "/clone-long", "/generate"])
def test_in_its_slow_hours_apiyi_carries_a_warning_on_the_select(
    client, slow_hours, page
):
    html = client.get(page).text

    assert (
        f'value="apiyi" data-needs-key="true"\n                data-notice="{SLOW}"'
        in html
    )
    assert 'value="kie" data-needs-key="true"\n                data-notice=""' in html
    assert 'id="provider-notice" hidden' in html


def test_pictures_never_warns_its_images_go_inside_the_request(client, slow_hours):
    assert SLOW not in client.get("/pictures").text


def test_outside_its_slow_hours_apiyi_carries_no_warning(client, monkeypatch):
    monkeypatch.setenv("OFM_APIYI_SLOW_FROM_UTC_H", "0")
    monkeypatch.setenv("OFM_APIYI_SLOW_UNTIL_UTC_H", "0")

    assert "can be slow" not in client.get("/clone").text


def _selected(html: str) -> str:
    found = re.search(r'<option value="([^"]+)"[^>]*\bselected\b', html)
    assert found is not None
    return found.group(1)


def _ran(task: str, provider: str, actor: str) -> None:
    create_job(task, {"provider": provider, "prompt": "p"}, actor=actor)


@pytest.mark.parametrize("page", ["/generate", "/clone"])
def test_higgsfield_is_the_default_provider(client, page):
    assert _selected(client.get(page).text) == "higgsfield"


@pytest.mark.parametrize("page", ["/generate", "/clone"])
def test_default_provider_follows_the_setting(client, page, monkeypatch):
    monkeypatch.setenv("OFM_DEFAULT_PROVIDER", "kie")

    assert _selected(client.get(page).text) == "kie"


@pytest.mark.parametrize("page", CLONE_PAGES)
def test_a_clone_page_starts_on_the_provider_of_the_last_clone(client, actor, page):
    for provider in ("higgsfield", "higgsfield", "kie", "apiyi"):
        _ran("clone", provider, actor)

    assert _selected(client.get(page).text) == "apiyi"


def test_a_clone_links_run_counts_for_clone(client, actor):
    _ran("clone", "kie", actor)
    _ran("clone_links", "apiyi", actor)

    assert _selected(client.get("/clone").text) == "apiyi"


def test_generate_starts_on_the_provider_of_the_last_ai_run(client, actor):
    _ran("ai", "apiyi", actor)
    _ran("clone", "kie", actor)

    assert _selected(client.get("/generate").text) == "apiyi"


def test_a_last_provider_the_page_does_not_offer_is_passed_over(client, actor):
    """/clone-long has no RunningHub: the newest run it can take wins."""
    _ran("clone", "apiyi", actor)
    _ran("clone", "runninghub", actor)

    assert _selected(client.get("/clone-long").text) == "apiyi"
    assert _selected(client.get("/clone").text) == "runninghub"


def test_pictures_starts_on_the_last_pictures_run(client, actor):
    _ran("pictures", "apiyi", actor)

    assert _selected(client.get("/pictures").text) == "apiyi"


def test_another_persons_runs_never_count(client):
    _ran("clone", "apiyi", "someone-else")

    assert _selected(client.get("/clone").text) == "higgsfield"
