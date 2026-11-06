"""/clone's subject tiles (features/clone/service.subject_picks): the first
image of a person's /clone and /clone-links runs, last used and most used,
one tile per picture (same content = one picture), only files still in
their own part of the store."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.clone.service import subject_picks
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import assets_root_for_user
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", root)
    return root


def _picture(root: Path, digest: str, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest * 64}__{name}.png"
    path.write_bytes(b"png")
    return path.resolve()


def _run(actor: str, *images: Path, task: str = "clone") -> None:
    create_job(task, {"reference_images": [str(p) for p in images]}, actor=actor)


def test_last_used_newest_first_then_most_used_most_first():
    user = make_user()
    root = assets_root_for_user(user.id)
    a, b, c = (_picture(root, d, d) for d in "abc")
    me = str(user.id)
    _run(me, a)
    _run(me, a)
    _run(me, b)
    _run(me, a)
    _run(me, c, b)  # c is this run's subject; b its second image

    picks = subject_picks(me, root)

    assert picks.last_used == [str(c), str(a), str(b)]
    assert picks.most_used == [str(a), str(c), str(b)]


def test_one_picture_under_two_names_is_one_tile_at_the_name_still_stored():
    user = make_user()
    root = assets_root_for_user(user.id)
    kept = _picture(root, "d", "kept")
    gone = root / f"{'d' * 64}__deleted.png"
    me = str(user.id)
    _run(me, gone)
    _run(me, kept)
    _run(me, gone)

    picks = subject_picks(me, root)

    assert picks.most_used == [str(kept)]
    assert picks.last_used == [str(kept)]


def test_a_picture_gone_from_the_store_is_left_out():
    user = make_user()
    root = assets_root_for_user(user.id)
    kept = _picture(root, "e", "kept")
    gone = _picture(root, "f", "gone")
    me = str(user.id)
    _run(me, gone)
    _run(me, kept)
    gone.unlink()

    assert subject_picks(me, root).last_used == [str(kept)]


def test_clone_links_runs_count_and_other_tools_do_not():
    user = make_user()
    root = assets_root_for_user(user.id)
    a, b = (_picture(root, d, d) for d in "ab")
    me = str(user.id)
    _run(me, a, task="clone_links")
    _run(me, b, task="ai")

    assert subject_picks(me, root).last_used == [str(a)]


def test_another_persons_runs_and_pictures_are_never_offered():
    me, other = make_user("me@example.com"), make_user("other@example.com")
    theirs = _picture(assets_root_for_user(other.id), "a", "theirs")
    _run(str(other.id), theirs)
    # Even a run of mine naming their file (a forged path) offers nothing.
    _run(str(me.id), theirs)

    picks = subject_picks(str(me.id), assets_root_for_user(me.id))

    assert picks.last_used == []
    assert picks.most_used == []


def test_the_clone_page_shows_the_tiles():
    client = TestClient(app)
    user = sign_in(client)
    picture = _picture(assets_root_for_user(user.id), "a", "face")
    _run(str(user.id), picture)

    page = client.get("/clone").text

    assert page.count('class="photo-tile subject-tile"') == 2  # last + most used
    assert f'data-path="{picture}"' in page
    assert "Last used" in page
    assert "Most used" in page


def test_a_person_with_no_runs_gets_no_tile_rows():
    client = TestClient(app)
    sign_in(client)

    assert "subject-tile" not in client.get("/clone").text
