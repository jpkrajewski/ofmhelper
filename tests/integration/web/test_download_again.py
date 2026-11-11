"""A download asked for again (features/downloads/again.py; owner
2026-11-11): the same link is never downloaded twice -- its card goes to the
first place -- and a video the retention sweep removed is fetched again into
its own job, by the card's Download, by the link pasted again, or by a
clone's Recreate (`/refs/missing` says which gone files can come back).

No network and no ffmpeg (`download_files.py`)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from download_files import DAY, backdate, fake_downloads, fake_posters, row, rows
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import create_job, get_job, list_jobs_page
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.downloads import cleanup, media
from ofmhelpers.features.jobs import restart
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.web.main import app

A = "https://x.example/video/a"
B = "https://x.example/video/b"
C = "https://x.example/video/c"
TASKS = {"download_media"}


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def downloads(tmp_path, monkeypatch):
    fake_posters(monkeypatch)
    return fake_downloads(monkeypatch, tmp_path)


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _paste(client: TestClient, *links: str) -> dict:
    r = client.post("/download-media/run", data={"urls": "\n".join(links)})
    assert r.status_code == 202, r.text
    return r.json()


def _shown(client: TestClient, url: str = "/download-assets") -> list[str]:
    """The jobs of the page's cards, in order, each once."""
    found = re.findall(r'data-job-id="([^"]+)"', client.get(url).text)
    return list(dict.fromkeys(found))


def _card(client: TestClient, job_id: str) -> str:
    """The job's first card, from the gallery's own fragment (the page adds
    the live cards' templates after the last one)."""
    html = client.get("/download-assets/gallery").text
    after = html.split(f'data-job-id="{job_id}"', 1)[1]
    return after.split('class="result-item"', 1)[0]


def _jobs(client: TestClient) -> int:
    actor = str(client.user.id)  # type: ignore[attr-defined]
    return list_jobs_page(TASKS, 0, 50, actor).total


def _remove_videos() -> None:
    """Two days on, the sweep takes every video and leaves its poster."""
    done = cleanup.sweep(datetime.now(UTC) + timedelta(seconds=2 * DAY))
    assert done.failed == 0


# --- the same link is not downloaded twice ---------------------------------------


def test_a_link_downloads_once_and_its_card_goes_first_when_asked_again(
    client, downloads
):
    first = _paste(client, A)["job_id"]
    second = _paste(client, B)["job_id"]
    assert _shown(client) == [second, first]

    again = _paste(client, A)

    assert again == {"job_id": None, "job_ids": [], "again": [first]}
    assert downloads.fetched == [A, B]
    assert _jobs(client) == 2
    assert _shown(client) == [first, second]
    # The page asks again and again: the order stays.
    assert _shown(client) == [first, second]
    assert _shown(client, "/download-assets/gallery") == [first, second]


def test_a_link_repeated_in_one_paste_downloads_once(client, downloads):
    queued = _paste(client, A, A, B, A)

    assert downloads.fetched == [A, B]
    assert get_job(queued["job_id"]).params == {"urls": [A, B]}
    assert [f.url for f in rows(queued["job_id"])] == [A, B]


def test_only_the_new_links_of_a_paste_make_a_job(client, downloads):
    first = _paste(client, A)["job_id"]

    mixed = _paste(client, A, C)

    assert mixed["again"] == [first]
    assert mixed["job_ids"] == [mixed["job_id"]]
    assert get_job(mixed["job_id"]).params == {"urls": [C]}
    assert downloads.fetched == [A, C]


def test_a_link_someone_else_downloaded_is_not_fetched_again_and_the_card_is_mine(
    client, downloads
):
    """Dev 2026-11-11: a VA pasted a reel their owner had downloaded five
    hours before and it was downloaded again. The same link is the same
    video whoever asked: the server's file is used, the asker gets a card
    and a job of their own, and never sees the other person's."""
    other = TestClient(app)
    theirs_user = sign_in(other, "other@example.com")
    theirs = _paste(other, A)["job_id"]

    mine = _paste(client, A)

    assert downloads.fetched == [A]  # asked of the site once
    assert mine["again"] == []
    assert mine["job_id"] not in (None, theirs)
    job = get_job(mine["job_id"])
    assert (job.status, job.user_id) == ("done", client.user.id)
    assert job.local_paths() == get_job(theirs).local_paths()
    assert _shown(client) == [mine["job_id"]]
    assert _shown(other) == [theirs]
    assert client.get(f"/download-media/jobs/{theirs}/status").status_code == 404
    # A copy of their own in their part of the asset store, not the other's.
    mine_row, theirs_row = row(mine["job_id"]), row(theirs)
    assert mine_row.user_id == client.user.id
    assert theirs_row.user_id == theirs_user.id
    assert f"users{os.sep}{client.user.id}{os.sep}" in mine_row.asset_path
    assert mine_row.asset_path != theirs_row.asset_path


def test_a_shared_file_stays_while_anyone_still_has_it(client, downloads):
    """Two people's cards of one file: the sweep takes the older card's
    video only on paper, the file stays for the one still in its keep time."""
    other = TestClient(app)
    sign_in(other, "other@example.com")
    theirs = _paste(other, A)["job_id"]
    backdate(theirs, 2 * DAY)
    mine = _paste(client, A)["job_id"]
    video = Path(get_job(mine).local_paths()[0])

    done = cleanup.sweep()

    assert done.failed == 0
    assert not row(theirs).stored
    assert row(mine).stored
    assert video.is_file()
    assert client.get(f"/download-media/files/{mine}/0").status_code == 200
    # Theirs comes back from the same file: nothing is asked of the site.
    r = other.post(f"/download-media/{theirs}/replays")
    assert r.status_code == 202
    assert row(theirs).stored
    assert downloads.fetched == [A]


def test_a_link_nobody_has_stored_any_more_is_fetched(client, downloads):
    other = TestClient(app)
    sign_in(other, "other@example.com")
    _paste(other, A)
    _remove_videos()

    _paste(client, A)

    assert downloads.fetched == [A, A]


def test_a_link_that_failed_is_tried_again_as_new(client, downloads):
    bad = "https://x.example/fail/1"
    first = _paste(client, bad)["job_id"]

    again = _paste(client, bad)

    assert rows(first) == []
    assert again["again"] == []
    assert again["job_id"] not in (None, first)


def test_a_hidden_download_asked_for_again_is_shown_again(client):
    job = _paste(client, A)["job_id"]
    assert client.post(f"/jobs/{job}/hidden", json={"hidden": True}).is_success
    assert _shown(client) == []

    _paste(client, A)

    assert _shown(client) == [job]


def test_a_removed_video_pasted_again_is_fetched_into_its_own_job(client, downloads):
    job = _paste(client, A)["job_id"]
    _remove_videos()
    assert row(job).stored is False

    again = _paste(client, A)

    assert again == {"job_id": None, "job_ids": [], "again": [job]}
    assert downloads.fetched == [A, A]
    assert _jobs(client) == 1
    assert row(job).stored is True
    assert Path(row(job).file_path).is_file()


# --- the card of a removed video, and its Download -------------------------------


def test_the_card_of_a_removed_video_shows_its_poster_and_one_download(client):
    job = _paste(client, A)["job_id"]
    assert "<video" in _card(client, job)
    assert "send-to-clone" in _card(client, job)
    _remove_videos()

    card = _card(client, job)

    files = f"/download-media/files/{job}/0"
    assert f'<img src="{files}/poster"' in card
    assert "<video" not in card
    assert "download-again" in card
    assert "send-to-clone" not in card  # back once the video is
    assert "download-btn" not in card
    assert "recreate-btn" in card
    assert client.get(f"{files}/poster").content == b"RIFF"
    gone = client.get(files)
    assert gone.status_code == 410
    assert gone.json() == {"detail": "Removed to save space: download it again"}
    assert client.get(f"{files}/preview").status_code == 410


def test_a_removed_file_keeps_its_place_among_the_jobs_files(client):
    """Two files, the first removed: the second is still file 1 on its card
    and at `/files/{job}/1`."""
    job = _paste(client, A, B)["job_id"]
    first, second = rows(job)
    # Only the first is due: the second was asked for after the round's time.
    store.touch(job, B, datetime.now(UTC) + timedelta(seconds=3 * DAY))
    _remove_videos()
    assert [f.stored for f in rows(job)] == [False, True]

    cards = client.get("/download-assets/gallery").text.split(f'data-job-id="{job}"')[
        1:
    ]

    assert len(cards) == 2
    assert 'data-file-index="0"' in cards[0]
    assert "download-again" in cards[0]
    assert 'data-file-index="1"' in cards[1]
    assert "download-again" not in cards[1]
    assert f"/download-media/files/{job}/1" in cards[1]
    served = client.get(f"/download-media/files/{job}/1")
    assert served.content == Path(second.file_path).read_bytes()
    assert not Path(first.file_path).exists()


def test_download_fetches_a_removed_video_into_the_same_job(client, downloads):
    job = _paste(client, A)["job_id"]
    before = row(job)
    _remove_videos()

    r = client.post(f"/download-media/{job}/replays")

    assert r.status_code == 202
    assert r.json() == {"job_id": job}
    assert downloads.fetched == [A, A]
    assert _jobs(client) == 1
    after = row(job)
    assert (after.id, after.created_at) == (before.id, before.created_at)
    assert (after.stored, after.removed_at) == (True, None)
    assert after.last_used_at > before.last_used_at
    assert after.file_path != before.file_path
    assert Path(after.file_path).is_file()
    assert Path(after.asset_path).is_file()
    run = get_job(job)
    assert run.status == "done"
    assert run.local_paths() == [after.file_path]
    assert "restarts" not in run.params
    assert client.get(f"/download-media/files/{job}/0").status_code == 200
    assert "<video" in _card(client, job)
    assert "send-to-clone" in _card(client, job)


def test_download_on_a_video_that_is_there_fetches_nothing(client, downloads):
    job = _paste(client, A)["job_id"]

    r = client.post(f"/download-media/{job}/replays")

    assert r.status_code == 202
    assert r.json() == {"job_id": job}
    assert downloads.fetched == [A]


def test_a_double_click_queues_the_job_once(client, downloads, monkeypatch):
    job = _paste(client, A)["job_id"]
    _remove_videos()
    queued = []
    monkeypatch.setattr(
        restart, "enqueue_to", lambda *args, **kwargs: queued.append(args)
    )

    answers = [client.post(f"/download-media/{job}/replays") for _ in range(2)]

    assert [(r.status_code, r.json()) for r in answers] == [(202, {"job_id": job})] * 2
    assert len(queued) == 1
    assert queued[0][2:] == (
        job,
        media.run_downloads,
        {
            "urls": [A],
            "assets_root": str(asset_store.assets_root_for_user(client.user.id)),
            "user_id": client.user.id,
            "job_id": job,
        },
    )
    assert get_job(job).status == "running"
    assert downloads.fetched == [A]


def test_someone_elses_job_is_not_found(client, downloads):
    other = TestClient(app)
    sign_in(other, "other@example.com")
    theirs = _paste(other, A)["job_id"]
    _remove_videos()

    r = client.post(f"/download-media/{theirs}/replays")

    assert r.status_code == 404
    assert client.get(f"/download-media/{theirs}/files/0/stored").status_code == 404
    assert downloads.fetched == [A]
    assert row(theirs).stored is False


def test_a_job_that_is_no_download_is_not_found(client):
    clone = create_job("clone", {}, actor=str(client.user.id))
    run_job(clone, list, {})

    assert client.post(f"/download-media/{clone}/replays").status_code == 404
    assert client.post("/download-media/nope/replays").status_code == 404


def test_a_video_gone_from_its_site_keeps_its_card_and_stays_removed(
    client, downloads, monkeypatch
):
    job = _paste(client, A)["job_id"]
    _remove_videos()
    monkeypatch.setattr(downloads, "_one", downloads.failing)

    r = client.post(f"/download-media/{job}/replays")

    assert r.status_code == 202
    assert get_job(job).status == "done"
    assert row(job).stored is False
    assert "download-again" in _card(client, job)
    status = client.get(f"/download-media/jobs/{job}/status").json()
    assert [(item["index"], item["removed"]) for item in status["result"]] == [
        (0, True)
    ]
    assert status["failed_sources"] == []


# --- a clone's Recreate ----------------------------------------------------------


def test_a_removed_download_is_restorable_and_comes_back_under_its_new_path(
    client, assets
):
    job = _paste(client, A)["job_id"]
    old = row(job).asset_path
    never = str(Path(old).with_name("0" * 64 + "__never.mp4"))
    _remove_videos()

    answer = client.get("/refs/missing", params={"path": [old, never]}).json()

    assert answer == {
        "missing": [old, never],
        "restorable": [{"path": old, "job_id": job, "index": 0}],
    }
    assert client.get(f"/download-media/{job}/files/0/stored").status_code == 404

    assert client.post(f"/download-media/{job}/replays").status_code == 202
    stored = client.get(f"/download-media/{job}/files/0/stored").json()["path"]

    # Fetched again it is other bytes, so the store names it differently.
    assert stored != old
    assert Path(stored).is_file()
    assert client.get("/refs/missing", params={"path": [stored]}).json() == {
        "missing": [],
        "restorable": [],
    }


def test_a_file_that_is_there_or_someone_elses_is_not_restorable(client):
    other = TestClient(app)
    sign_in(other, "other@example.com")
    theirs = _paste(other, A)["job_id"]
    their_path = row(theirs).asset_path
    mine = _paste(client, B)["job_id"]
    my_path = row(mine).asset_path
    cleanup.sweep(datetime.now(UTC) + timedelta(seconds=2 * DAY))
    assert client.post(f"/download-media/{mine}/replays").status_code == 202

    answer = client.get(
        "/refs/missing", params={"path": [their_path, row(mine).asset_path]}
    ).json()

    assert answer == {"missing": [their_path], "restorable": []}
    assert my_path != row(mine).asset_path
