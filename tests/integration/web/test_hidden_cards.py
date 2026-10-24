"""
Hide in a gallery card's ⋯ menu (/generate, /clone, /clone-long,
/download-assets): `POST /jobs/{id}/hidden` takes the card out of its
gallery's pages -- the job, its status page and its files stay. While at
least one is hidden the page offers "Show hidden (N)"; `?hidden=1` on the
gallery's fragment is the view of only the hidden cards, marked
`data-hidden` with Unhide in their menu. Only the owner can hide their job,
and a gallery only counts its own tasks' hidden jobs.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from signin import sign_in

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.generation import index as generate_index
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

ANN = "ann@example.com"
BOB = "bob@example.com"


class Gallery(BaseModel, frozen=True):
    page: str
    fragment: str
    task: str


GALLERIES = [
    Gallery(page="/generate", fragment="/generate/gallery", task="fake_ai"),
    Gallery(page="/clone", fragment="/clone/gallery", task="clone"),
    Gallery(page="/clone-long", fragment="/clone-long/gallery", task="clone_long"),
    Gallery(
        page="/download-assets",
        fragment="/download-assets/gallery",
        task="download_media",
    ),
]


def _client(email: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    return c, str(sign_in(c, email).id)


@pytest.fixture
def ann() -> tuple[TestClient, str]:
    return _client(ANN)


@pytest.fixture
def bob() -> tuple[TestClient, str]:
    return _client(BOB)


def _done(task: str, actor: str, out: Path) -> str:
    out.write_bytes(b"png")
    job_id = create_job(task, {"prompt": out.stem}, actor=actor)
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})
    return job_id


def _failed(task: str, actor: str) -> str:
    job_id = create_job(task, {"prompt": "x"}, actor=actor)

    def boom():
        msg = "Wrong API Key"
        raise RuntimeError(msg)

    run_job(job_id, boom, {})
    return job_id


def _hide(client: TestClient, job_id: str, hidden: bool = True):
    return client.post(f"/jobs/{job_id}/hidden", json={"hidden": hidden})


def _ids(html: str) -> list[str]:
    return re.findall(r'class="result-item" data-job-id="([^"]+)"', html)


def _hidden_ids(html: str) -> list[str]:
    return re.findall(
        r'class="result-item" data-job-id="([^"]+)"[^>]*data-params=\'[^\']*\' data-hidden',
        html,
    )


def _tools(html: str) -> str:
    """The toolbar row under the page title (like /phones' Export, Add
    phone), "Show hidden" at its right -- not inside the gallery."""
    found = re.search(
        r'<div class="ph-toolbar"[^>]*>\s*<div class="toolbar-actions">.*?</div>\s*</div>',
        html,
        re.DOTALL,
    )
    assert found, "no toolbar with Show hidden"
    assert "hidden-toggle" in found.group(0)
    return found.group(0)


@pytest.mark.parametrize("gallery", GALLERIES, ids=lambda g: g.page)
def test_hide_takes_the_card_out_and_show_hidden_brings_it_back(ann, tmp_path, gallery):
    client, actor = ann
    kept = _done(gallery.task, actor, tmp_path / "kept.png")
    gone = _done(gallery.task, actor, tmp_path / "gone.png")

    r = _hide(client, gone)
    assert r.status_code == 200, r.text
    assert r.json() == {"hidden": True}

    page = client.get(gallery.page).text
    assert _ids(page) == [kept]
    tools = _tools(page)
    assert 'data-hidden-count="1"' in tools
    assert tools.startswith('<div class="ph-toolbar">')
    assert "Show hidden (1)" in tools
    # A plain button, bordered like every other: not the borderless ghost.
    assert 'class="btn hidden-toggle"' in tools
    # Between the title and the form + gallery, not inside the gallery.
    assert (
        page.index("page-head")
        < page.index("ph-toolbar")
        < page.index("generate-layout")
    )
    assert page.count("hidden-toggle") == 1
    assert "js/hidden-cards.js" in page
    assert f'data-gallery-endpoint="{gallery.fragment}"' in page

    assert _ids(client.get(gallery.fragment).text) == [kept]
    # Show hidden: only the hidden ones.
    shown = client.get(f"{gallery.fragment}?hidden=1").text
    assert _ids(shown) == [gone]
    assert _hidden_ids(shown) == [gone]


@pytest.mark.parametrize("gallery", GALLERIES, ids=lambda g: g.page)
def test_unhide_puts_it_back_and_the_toggle_goes(ann, tmp_path, gallery):
    client, actor = ann
    job = _done(gallery.task, actor, tmp_path / "a.png")
    assert _hide(client, job).json() == {"hidden": True}

    r = _hide(client, job, hidden=False)
    assert r.json() == {"hidden": False}

    page = client.get(gallery.page).text
    assert _ids(page) == [job]
    assert _hidden_ids(page) == []
    tools = _tools(page)
    assert 'data-hidden-count="0"' in tools
    assert tools.startswith('<div class="ph-toolbar" hidden>')


def test_hidden_cards_carry_unhide_and_shown_ones_hide(ann, tmp_path):
    client, actor = ann
    done = _done("fake_ai", actor, tmp_path / "a.png")
    failed = _failed("fake_ai", actor)
    _hide(client, done)

    html = client.get("/generate/gallery?hidden=1").text + client.get("/generate").text
    cards = dict(
        re.findall(
            r'data-job-id="([^"]+)".*?(<div class="result-actions">.*?</details>)',
            html,
            re.DOTALL,
        )
    )
    # A hidden card: Hide put away, Unhide offered -- and the other way round.
    assert re.search(r'class="menu-item hide-btn" hidden>', cards[done])
    assert re.search(r'class="menu-item unhide-btn">', cards[done])
    # A failed card now has a menu too, for Hide.
    assert "recreate-btn" in cards[failed]
    assert re.search(r'class="menu-item hide-btn">', cards[failed])
    assert re.search(r'class="menu-item unhide-btn" hidden>', cards[failed])


def test_live_cards_get_hide_from_the_page_templates(ann):
    """generation.js clones these rows into a card that finishes while the
    page is open: Hide must be there as on a reloaded card."""
    client, _ = ann
    html = client.get("/generate").text
    for template in ("card-actions-done", "card-actions-failed"):
        row = re.search(f'<template id="{template}">(.*?)</template>', html, re.DOTALL)
        assert row, template
        assert 'class="menu-item hide-btn">' in row.group(1)
        assert 'class="menu-item unhide-btn" hidden>' in row.group(1)


def test_only_the_owner_can_hide_their_job(ann, bob, tmp_path):
    ann_client, ann_actor = ann
    bob_client, _ = bob
    job = _done("fake_ai", ann_actor, tmp_path / "a.png")

    assert _hide(bob_client, job).status_code == 404
    assert get_job(job).hidden_at is None
    assert _ids(ann_client.get("/generate").text) == [job]


def test_hiding_a_job_that_does_not_exist_is_404(ann):
    client, _ = ann
    assert _hide(client, "nope").status_code == 404


def test_the_body_must_say_hidden_or_not(ann, tmp_path):
    client, actor = ann
    job = _done("fake_ai", actor, tmp_path / "a.png")
    assert client.post(f"/jobs/{job}/hidden", json={}).status_code == 422


def test_hiding_keeps_the_job_its_status_page_and_files(ann, tmp_path):
    client, actor = ann
    job = _done("fake_ai", actor, tmp_path / "a.png")
    _hide(client, job)

    assert get_job(job, actor) is not None
    assert client.get(f"/ai/jobs/{job}").status_code == 200
    assert client.get(f"/ai/files/{job}/0").status_code == 200


def test_hiding_twice_keeps_when_it_was_first_hidden(ann, tmp_path):
    client, actor = ann
    job = _done("fake_ai", actor, tmp_path / "a.png")
    _hide(client, job)
    first = get_job(job).hidden_at
    assert first is not None

    assert _hide(client, job).json() == {"hidden": True}
    assert get_job(job).hidden_at == first


def test_a_gallery_counts_only_its_own_hidden_jobs(ann, tmp_path):
    client, actor = ann
    clone = _done("clone", actor, tmp_path / "c.png")
    _hide(client, clone)

    assert 'data-hidden-count="1"' in _tools(client.get("/clone").text)
    assert 'data-hidden-count="0"' in _tools(client.get("/generate").text)


def test_another_persons_hidden_jobs_are_not_counted(ann, bob, tmp_path):
    bob_client, bob_actor = bob
    _hide(bob_client, _done("fake_ai", bob_actor, tmp_path / "b.png"))
    ann_client, _ = ann
    assert 'data-hidden-count="0"' in _tools(ann_client.get("/generate").text)


def test_live_polling_leaves_hidden_cards_out(ann, tmp_path):
    """live-gallery.js prepends what /latest has and the page lacks: a hidden
    card must not come back that way."""
    client, actor = ann
    kept = _done("fake_ai", actor, tmp_path / "kept.png")
    gone = _done("fake_ai", actor, tmp_path / "gone.png")
    _hide(client, gone)
    assert _ids(client.get("/generate/gallery/latest").text) == [kept]


def test_each_view_pages_over_its_own_cards(ann, tmp_path, monkeypatch):
    """The normal view pages over the shown jobs, the hidden view over the
    hidden ones: no sentinel pointing at a page of the other view's cards."""
    monkeypatch.setattr(generate_index, "GALLERY_LIMIT", 3)
    client, actor = ann
    jobs = [_done("fake_ai", actor, tmp_path / f"{i}.png") for i in range(8)]
    for job in jobs[4:]:
        _hide(client, job)

    page = client.get("/generate").text
    assert _ids(page) == list(reversed(jobs[1:4]))
    assert 'data-next-offset="3"' in page
    rest = client.get("/generate/gallery?offset=3").text
    assert _ids(rest) == [jobs[0]]
    assert "gallery-sentinel" not in rest

    shown = client.get("/generate/gallery?hidden=1").text
    assert _ids(shown) == list(reversed(jobs[5:]))
    assert 'data-next-offset="3"' in shown
    more = client.get("/generate/gallery?hidden=1&offset=3").text
    assert _ids(more) == [jobs[4]]
    assert "gallery-sentinel" not in more


def test_the_empty_text_waits_while_cards_are_only_hidden(ann, tmp_path):
    """Every clone hidden: not "No clones yet", "Show hidden (1)"."""
    client, actor = ann
    _hide(client, _done("clone", actor, tmp_path / "c.png"))
    html = client.get("/clone").text
    assert re.search(r'<p class="empty-state" id="clone-empty" hidden>', html)
