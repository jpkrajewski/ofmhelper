"""/download-assets "Download from Drive sheet" (features/downloads/sheet.py;
owner 2026-11-12): a click reads today's column of the links sheet in the
person's own Drive root and downloads every link not downloaded or queued
before as one download job; the button then rests.

The Drive is the fake one, the downloads write small files
(`download_files.py`): no network."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from download_files import fake_downloads, fake_posters, rows
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisDown
from signin import make_user, sign_in

from ofmhelpers.cache import get_redis
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import get_job, list_jobs_page
from ofmhelpers.db.stores.users import _repository as user_repository
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.downloads import service, sheet
from ofmhelpers.features.drive.folders import root_name
from ofmhelpers.integrations.gdrive import RECONNECT, DriveError
from ofmhelpers.integrations.gdrive.fake import FakeDrive, fake_drive, fake_person
from ofmhelpers.integrations.gdrive.models import SheetTab
from ofmhelpers.schemas import Role, UserForm
from ofmhelpers.web.main import app

OWNER = "owner@example.com"
OTHER = "other@example.com"
VA = "va@example.com"
A = "https://www.instagram.com/reel/aaa/?cplk=one"
B = "https://www.instagram.com/reel/bbb/?mdxt=two"
C = "https://www.instagram.com/reel/ccc/"
OLD = "https://www.instagram.com/reel/yesterday/"
BROKEN = "https://www.instagram.com/reel/fail/"
URL = "/download-media/sheet-downloads"
TASKS = {"download_media"}


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def drive(fake_externals) -> FakeDrive:
    return fake_drive()


@pytest.fixture(autouse=True)
def downloads(drive, tmp_path, monkeypatch):
    fake_posters(monkeypatch)
    return fake_downloads(monkeypatch, tmp_path)


def _client(email: str = OWNER, *, google: bool = True) -> TestClient:
    c = TestClient(app)
    c.user = sign_in(c, email)  # type: ignore[attr-defined]
    if google:
        user_repository().signed_in(c.user.id, "", f"fake-refresh:{email}")  # type: ignore[attr-defined]
    return c


def _root(email: str) -> str:
    """The person's root folder in their own Drive, made by hand."""
    person = fake_person(f"fake-refresh:{email}")
    person.children("root")
    found = fake_drive().named(root_name(), person.my_drive)
    if found:
        return found[0]
    return fake_drive().seed_folder(root_name(), person.my_drive, owner=email)


def _type(email: str, *today: str, name: str = "content_links") -> None:
    """The sheet as people left it: yesterday's column, then today's with
    `today` under it."""
    day = sheet.today()
    cells: list[list[date | str | None]] = [[day - timedelta(days=1), day]]
    cells.extend([OLD, link] for link in today)
    tab = SheetTab(name="Sheet1", rows=cells)
    fake_drive().add_sheet(_root(email), name, [tab])


def _click(client: TestClient) -> dict:
    r = client.post(URL)
    assert r.status_code == 200, r.text
    return r.json()


def _rested(client: TestClient) -> None:
    """A minute later: the button works again."""
    sheet.Clicker(user_id=client.user.id).wake()  # type: ignore[attr-defined]


def _jobs(client: TestClient) -> int:
    actor = str(client.user.id)  # type: ignore[attr-defined]
    return list_jobs_page(TASKS, 0, 50, actor).total


# --- the click -------------------------------------------------------------------


def test_a_click_downloads_todays_links_as_one_job(downloads):
    client = _client()
    _type(OWNER, A, B)

    answer = _click(client)

    day = sheet.today().isoformat()
    assert answer["new"] == 2
    assert answer["already"] == 0
    assert answer["skipped"] == 0
    assert answer["day"] == day
    assert 0 < answer["cooldown_s"] <= 60
    job = get_job(answer["job_id"])
    assert job.status == "done"
    assert job.task == "download_media"
    assert job.user_id == client.user.id
    assert job.params == {"urls": [A, B], "sheet_date": day}
    # Yesterday's column is not today's.
    assert downloads.fetched == [A, B]
    assert [f.url for f in rows(job.id)] == [A, B]
    assert f'data-job-id="{job.id}"' in client.get("/download-assets").text


def test_a_second_click_downloads_nothing_twice(downloads):
    client = _client()
    _type(OWNER, A, B)
    first = _click(client)["job_id"]
    _rested(client)

    again = _click(client)

    assert again["job_id"] is None
    assert (again["new"], again["already"]) == (0, 2)
    assert downloads.fetched == [A, B]
    assert _jobs(client) == 1
    assert get_job(first).status == "done"


def test_only_the_links_added_since_are_downloaded(downloads):
    client = _client()
    _type(OWNER, A)
    _click(client)
    _type(OWNER, A, B, C)
    _rested(client)

    added = _click(client)

    assert (added["new"], added["already"]) == (2, 1)
    assert get_job(added["job_id"]).params["urls"] == [B, C]
    assert downloads.fetched == [A, B, C]
    assert _jobs(client) == 2


def test_a_link_pasted_by_hand_before_is_not_downloaded_again(downloads):
    client = _client()
    pasted = client.post("/download-media/run", data={"urls": A}).json()["job_id"]
    _type(OWNER, A, B)

    answer = _click(client)

    assert (answer["new"], answer["already"]) == (1, 1)
    assert get_job(answer["job_id"]).params["urls"] == [B]
    assert downloads.fetched == [A, B]
    assert [f.url for f in rows(pasted)] == [A]


def test_a_link_that_failed_is_not_queued_by_every_click(downloads):
    """It has no `downloads` row to tell by: the click remembers it."""
    client = _client()
    _type(OWNER, BROKEN, A)
    first = _click(client)
    assert first["new"] == 2
    _rested(client)

    again = _click(client)

    assert again["job_id"] is None
    assert (again["new"], again["already"]) == (0, 2)
    assert downloads.fetched == [BROKEN, A]


def test_a_cell_that_is_no_link_is_counted_and_the_links_still_download(downloads):
    client = _client()
    _type(OWNER, A, "posted already", B)

    answer = _click(client)

    assert (answer["new"], answer["skipped"]) == (2, 1)
    assert downloads.fetched == [A, B]


def test_a_day_with_no_links_starts_nothing(downloads):
    client = _client()
    _type(OWNER)

    answer = _click(client)

    assert answer["job_id"] is None
    assert (answer["new"], answer["already"]) == (0, 0)
    assert _jobs(client) == 0


def test_links_queued_stay_new_when_the_queue_failed(downloads, monkeypatch):
    client = _client()
    _type(OWNER, A)

    def down(*_args: object, **_kwargs: object) -> None:
        msg = "queue is down"
        raise ConnectionError(msg)

    with monkeypatch.context() as broken:
        broken.setattr(service, "enqueue", down)
        with pytest.raises(ConnectionError, match="queue is down"):
            client.post(URL)
    assert downloads.fetched == []
    _rested(client)

    answer = _click(client)

    assert answer["new"] == 1
    assert downloads.fetched == [A]


# --- the cooldown ----------------------------------------------------------------


def test_the_button_rests_after_a_click_and_drive_is_not_asked(drive, downloads):
    client = _client()
    _type(OWNER, A)
    _click(client)
    _type(OWNER, A, B)
    drive.clear_calls()

    r = client.post(URL)

    assert r.status_code == 429
    assert re.fullmatch(r"Wait \d+ s", r.json()["detail"])
    assert drive.calls == []
    assert downloads.fetched == [A]


def test_a_refused_click_does_not_rest_the_button(downloads):
    """Nothing was read: once the person fixed what it named, the next
    click works at once."""
    client = _client()
    assert client.post(URL).status_code == 404
    assert client.post(URL).status_code == 404
    page = client.get("/download-assets").text
    assert 'sheet-download" data-cooldown-s="0"' in page

    _type(OWNER, A)

    assert _click(client)["new"] == 1


def test_links_marked_before_redis_failed_are_unmarked(downloads, monkeypatch):
    """A claim cut off part-way must not hide its first links for days."""
    client = _client()
    _type(OWNER, A, B)
    redis = get_redis()
    real_sadd = type(redis).sadd
    seen: list[str] = []

    def flaky(self, key, *values):
        seen.extend(values)
        if len(seen) > 1:
            msg = "redis went away"
            raise RedisDown(msg)
        return real_sadd(self, key, *values)

    with monkeypatch.context() as broken:
        broken.setattr(type(redis), "sadd", flaky)
        r = client.post(URL)
    assert r.status_code == 503
    assert r.json() == {"detail": "Could not start the download. Try again."}
    assert seen == [A, B]
    assert downloads.fetched == []
    _rested(client)

    answer = _click(client)

    assert (answer["new"], answer["already"]) == (2, 0)
    assert downloads.fetched == [A, B]


def test_one_persons_rest_is_not_anothers(downloads):
    owner, other = _client(OWNER), _client(OTHER)
    _type(OWNER, A)
    _type(OTHER, B)
    _click(owner)

    theirs = _click(other)

    assert get_job(theirs["job_id"]).params["urls"] == [B]
    assert get_job(theirs["job_id"]).user_id == other.user.id
    assert downloads.fetched == [A, B]


def test_the_page_draws_the_button_with_what_is_left_of_its_rest():
    client = _client()
    _type(OWNER, A)

    before = client.get("/download-assets").text
    assert 'class="btn sheet-download" data-cooldown-s="0"' in before
    assert before.index("sheet-download") < before.index("hidden-toggle")

    _click(client)

    after = client.get("/download-assets").text
    left = int(re.search(r'sheet-download" data-cooldown-s="(\d+)"', after).group(1))
    assert 0 < left <= 60


def test_with_nothing_hidden_the_row_stays_and_only_show_hidden_goes():
    client = _client()

    page = client.get("/download-assets").text

    assert '<div class="ph-toolbar" data-keep>' in page
    assert 'data-hidden-count="0" aria-pressed="false" hidden>' in page
    assert 'sheet-download" data-cooldown-s="0">' in page


def test_other_gallery_pages_have_no_sheet_button():
    client = _client()

    assert "sheet-download" not in client.get("/clone").text
    assert "sheet-download" not in client.get("/generate").text


# --- whose sheet -----------------------------------------------------------------


def test_everyone_reads_the_sheet_in_their_own_drive(downloads):
    owner, other = _client(OWNER), _client(OTHER)
    _type(OWNER, A)
    _root(OTHER)

    r = other.post(URL)

    assert r.status_code == 404
    assert r.json()["detail"] == (
        f"No sheet named content_links in {root_name()} in Google Drive"
    )
    assert downloads.fetched == []
    assert _click(owner)["new"] == 1


def test_a_va_reads_the_employers_sheet_and_the_job_is_the_vas(downloads):
    owner = make_user(OWNER)
    user_repository().signed_in(owner.id, "", f"fake-refresh:{OWNER}")
    assert users.add_user(UserForm(email=VA, role=Role.VA, owner_id=owner.id))
    va = TestClient(app)
    va.user = sign_in(va, VA, Role.VA)  # type: ignore[attr-defined]
    _type(OWNER, A)

    answer = _click(va)

    assert answer["new"] == 1
    assert get_job(answer["job_id"]).user_id == va.user.id  # type: ignore[attr-defined]
    assert downloads.fetched == [A]


# --- refusals --------------------------------------------------------------------


def test_without_google_drive_the_click_says_to_connect_it(downloads):
    client = _client(google=False)

    r = client.post(URL)

    assert r.status_code == 400
    assert r.json() == {"detail": "Connect Google Drive on your Account page"}


def test_no_root_folder_and_no_sheet_say_which_sheet_is_missing():
    client = _client()
    wanted = f"No sheet named content_links in {root_name()} in Google Drive"

    r = client.post(URL)
    assert (r.status_code, r.json()["detail"]) == (404, wanted)

    _type(OWNER, A, name="some other sheet")
    r = client.post(URL)
    assert (r.status_code, r.json()["detail"]) == (404, wanted)


def test_a_sheet_without_todays_date_says_so(drive, downloads):
    client = _client()
    day = sheet.today()
    tab = SheetTab(name="Sheet1", rows=[[day - timedelta(days=1)], [OLD]])
    drive.add_sheet(_root(OWNER), "content_links", [tab])

    r = client.post(URL)

    assert r.status_code == 404
    assert r.json() == {"detail": f"No column for {day:%d/%m/%Y} in content_links"}
    assert downloads.fetched == []


def test_the_sheet_is_found_by_the_configured_name(downloads, monkeypatch):
    monkeypatch.setenv("OFM_DOWNLOADS_SHEET_NAME", "links for today")
    client = _client()
    _type(OWNER, A, name="links for today")

    assert _click(client)["new"] == 1


def test_a_refused_google_sign_in_says_to_reconnect(drive, downloads):
    client = _client()
    _type(OWNER, A)
    drive.refuse_sign_in(OWNER)

    r = client.post(URL)

    assert r.status_code == 400
    assert r.json() == {"detail": RECONNECT}
    assert downloads.fetched == []


def test_a_drive_failure_is_one_plain_sentence_and_nothing_is_marked(
    downloads, monkeypatch
):
    client = _client()
    _type(OWNER, A)

    def outage(_self: FakeDrive, file_id: str) -> None:
        msg = f"Sheet {file_id}: <HttpError 500 backendError>"
        raise DriveError(msg)

    with monkeypatch.context() as broken:
        broken.setattr(FakeDrive, "sheet", outage)
        r = client.post(URL)
    assert r.status_code == 502
    assert r.json() == {"detail": "Google Drive did not answer. Try again."}

    assert _click(client)["new"] == 1
    assert downloads.fetched == [A]


def test_signed_out_the_click_is_refused():
    r = TestClient(app).post(URL, follow_redirects=False)

    assert r.status_code in (303, 401)
