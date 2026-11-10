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

CLONE_PAGES = ["/clone", "/clone-links", "/clone-long"]


@pytest.fixture
def client():
    with TestClient(app) as c:
        sign_in(c)
        yield c


@pytest.fixture
def actor(client) -> str:
    return str(sign_in(client).id)


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
