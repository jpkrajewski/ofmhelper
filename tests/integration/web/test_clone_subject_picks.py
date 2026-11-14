"""/clone's subject tiles (features/clone/service.subject_picks): the first
image of a person's /clone and /clone-links runs, last used and most used,
one tile per picture (same content = one picture), only files still in
their own part of the store."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.clone.service import subject_picks
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.schemas import Role, User
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import assets_root_for_user


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: root)
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


# --- a team: the owner and their VAs (owner 2026-11-14) -------------------------


def _team(owner_email: str, *va_emails: str) -> list[User]:
    owner = make_user(owner_email)
    vas = [
        users.set_owner(make_user(email, Role.VA).id, owner.id) for email in va_emails
    ]
    return [owner, *(va for va in vas if va is not None)]


def _tiles(html: str) -> list[str]:
    return re.findall(r'class="photo-tile subject-tile"[^>]*data-path="([^"]+)"', html)


def test_a_va_gets_the_owners_and_the_other_vas_subjects_and_their_uses_count():
    """ "va should get the same most used images on clone and lates litterlay
    the same, and their coutn shousl be also included in the most used"."""
    owner, va, other_va = _team(
        "boss@example.com", "va1@example.com", "va2@example.com"
    )
    root = assets_root_for_user(owner.id)
    a, b, c = (_picture(root, d, d) for d in "abc")
    _run(str(owner.id), a)
    _run(str(va.id), b)
    _run(str(va.id), b)
    _run(str(other_va.id), c)

    team = viewer_for(va, None).team
    picks = subject_picks(team, root)

    assert team == {str(owner.id), str(va.id), str(other_va.id)}
    assert viewer_for(owner, None).team == team
    assert picks.last_used == [str(c), str(b), str(a)]
    # b: the VA's two runs put it first on everyone's row, the owner's too.
    assert picks.most_used == [str(b), str(c), str(a)]


def test_the_owner_and_the_va_see_the_same_tiles_on_the_page():
    owner, va = _team("boss@example.com", "va1@example.com")
    root = assets_root_for_user(owner.id)
    a, b = (_picture(root, d, d) for d in "ab")
    _run(str(owner.id), a)
    _run(str(va.id), b)
    as_owner, as_va = TestClient(app), TestClient(app)
    sign_in(as_owner, owner.email)
    sign_in(as_va, va.email, Role.VA)

    seen_by_owner = _tiles(as_owner.get("/clone").text)

    assert seen_by_owner == [str(b), str(a), str(b), str(a)]  # last, then most used
    assert _tiles(as_va.get("/clone").text) == seen_by_owner


def test_another_owners_va_shares_nothing():
    _owner, va = _team("boss@example.com", "va1@example.com")
    theirs, their_va = _team("second@example.com", "va2@example.com")
    picture = _picture(assets_root_for_user(theirs.id), "a", "theirs")
    _run(str(their_va.id), picture)

    picks = subject_picks(viewer_for(va, None).team, assets_root_for_user(va.id))

    assert (picks.last_used, picks.most_used) == ([], [])


def test_a_va_nobody_owns_is_a_team_of_one():
    va = make_user("lone-va@example.com", Role.VA)

    assert viewer_for(va, None).team == {str(va.id)}
