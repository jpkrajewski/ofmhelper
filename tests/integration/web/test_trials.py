"""/trials: /drive's phone cards; Generate trials looks for new videos in a
login's POSTED and makes a captioned copy of each not made yet (one job per
folder at a time); the phone opens the folder, newest first: every copy
playable (an H.264 preview of the HEVC copy, a poster), five captions to choose
from or its own to write, a tick to upload; Upload replaces what is in TRIALS
next to POSTED with the ticked ones. What went wrong is said in short words,
never the exception."""

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
import worker_tasks
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_agency

from ofmhelpers.cache import JobQueue, QueueName, get_queue, get_redis
from ofmhelpers.db.stores import devices, users
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs import recovery, sweeps
from ofmhelpers.features.previews.render import write_video_thumb
from ofmhelpers.features.trials import cleanup, media, tasks
from ofmhelpers.features.trials import state as trial_state
from ofmhelpers.features.trials.captions import ReelNotes
from ofmhelpers.integrations.gdrive import DriveError, DriveFile
from ofmhelpers.integrations.gdrive.fake import FakeDrive, FakePersonDrive, fake_drive
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.video_tools import trials as trials_router

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"
SECOND_TO_FIFTH = ["second one", "third one", "fourth one", "fifth one"]


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    """The fake Drive, the agency's Google Drive connected with its
    "DEV - ALL ACCOUNTS" root (as /drive's tests)."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


@pytest.fixture
def made(monkeypatch) -> list[str]:
    """The snapchat pipeline, the AI and the page's previews, faked: a copy
    is its caption's bytes; the captions it was made with, in order."""
    captions: list[str] = []

    def pipeline(src, out, *, caption, seed):
        out.write_bytes(caption.encode())
        captions.append(caption)

    asked = iter(range(1000))
    monkeypatch.setattr(tasks, "pipeline_run", pipeline)
    monkeypatch.setattr(tasks, "understand", lambda src: ReelNotes(description="d"))
    monkeypatch.setattr(
        tasks,
        "captions_for",
        lambda notes: [f"caption {next(asked)}", *SECOND_TO_FIFTH],
    )
    monkeypatch.setattr(media, "preview", lambda copy: copy)
    monkeypatch.setattr(media, "poster", lambda copy: copy)
    return captions


def _client() -> TestClient:
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _posted(drive: FakeDrive) -> str:
    (account,) = drive.named("ann.ig - Anna", ROOT)
    (posted,) = drive.named("POSTED", account)
    return posted


def _login(drive: FakeDrive, *videos: str) -> int:
    """A phone with @ann.ig, its folders made, `videos` in its POSTED and
    read by a sweep (what /trials shows)."""
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Anna"
        )
    )
    added = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig")
    )
    assert added is not None
    drive_folders.sync()
    for name in videos:
        drive.add_file(_posted(drive), name, "video/mp4")
    drive_folders.sync()
    return added.id


def _state(login_id: int) -> trial_state.FolderState:
    return trial_state.load(users.agency_id(), login_id)


def _form_depth(html: str) -> int:
    """The deepest <form> nesting (a nested form is dropped by browsers:
    its buttons submit the outer one)."""

    class Forms(HTMLParser):
        depth = deepest = 0

        def handle_starttag(self, tag, _attrs):
            if tag == "form":
                self.depth += 1
                self.deepest = max(self.deepest, self.depth)

        def handle_endtag(self, tag):
            if tag == "form":
                self.depth -= 1

    parser = Forms()
    parser.feed(html)
    return parser.deepest


def test_trials_page_is_the_drive_cards_with_generate_trials(drive):
    login = _login(drive, "a.mp4", "b.mp4")

    page = _client().get("/trials").text

    assert f'href="/trials/{login}"' in page  # the phone opens the folder
    assert "@ann.ig" in page
    assert "Posted: <strong>2</strong>" in page
    assert "Trials: <strong>0</strong>" in page
    assert f'action="/trials/{login}/generate"' in page
    assert "Generate trials" in page
    assert "See generated" not in page  # nothing made yet


def test_generate_downloads_and_makes_every_posted_video_once(drive, made):
    login = _login(drive, "a.mp4", "b.mp4")
    drive.add_file(_posted(drive), "cover.jpg", "image/jpeg")
    drive_folders.sync()
    client = _client()

    r = client.post(f"/trials/{login}/generate")

    assert r.json() == {"started": True}  # answered at once; the page polls
    state = _state(login)
    assert sorted(r.name for r in state.reels) == ["a.mp4", "b.mp4"]  # no image
    assert all(r.out and r.status == "generated" and r.made == 1 for r in state.reels)
    assert sorted(made) == ["caption 0", "caption 1"]  # side by side: any order
    # Nothing left to make: a second click makes nothing again.
    client.post(f"/trials/{login}/generate")
    assert len(made) == 2
    cards = client.get("/trials").text
    assert "Trials: <strong>2</strong>" in cards
    assert "See generated" in cards


def test_folder_page_shows_each_copy_playable_with_its_caption_and_a_tick(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels

    page = client.get(f"/trials/{login}").text

    src = f"/trials/files/{login}/{reel.key}"
    assert f'src="{src}?v=1"' in page
    assert f'poster="{src}/poster?v=1"' in page
    assert f'name="approved" value="{reel.key}" form="tr-upload"' in page
    assert ">caption 0</textarea>" in page
    assert _form_depth(page) == 1
    assert client.get(f"{src}?v=1").content == b"caption 0"
    assert client.get(f"{src}/poster").status_code == 200


def test_page_preview_failing_serves_the_copy_itself(drive, made, monkeypatch):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels

    def broken(copy):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(media, "preview", broken)
    monkeypatch.setattr(media, "poster", broken)

    assert client.get(f"/trials/files/{login}/{reel.key}").content == b"caption 0"
    assert client.get(f"/trials/files/{login}/{reel.key}/poster").status_code == 404


def test_own_caption_makes_the_copy_again_with_it(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels

    client.post(
        f"/trials/{login}/reel/{reel.key}/caption", data={"caption": "  my   own "}
    )

    assert made[-1] == "my own"
    (again,) = _state(login).reels
    assert again.caption == "my own"
    assert again.made == 2
    assert '?v=2"' in client.get(f"/trials/{login}").text


def test_regenerate_runs_only_the_pipeline_with_a_new_seed(drive, made, monkeypatch):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    seeds: list[int] = []
    pipeline = tasks.pipeline_run

    def recording(src, out, *, caption, seed):
        seeds.append(seed)
        pipeline(src, out, caption=caption, seed=seed)

    def no_ai(*_args, **_kwargs):
        msg = "Regenerate asked the AI"
        raise AssertionError(msg)

    monkeypatch.setattr(tasks, "pipeline_run", recording)
    monkeypatch.setattr(tasks, "understand", no_ai)
    monkeypatch.setattr(tasks, "captions_for", no_ai)

    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")
    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    (again,) = _state(login).reels
    assert again.caption == "caption 0"
    assert made == ["caption 0"] * 3
    assert again.made == 3
    assert len(set(seeds)) == 2  # a new variant each time
    assert '?v=3"' in client.get(f"/trials/{login}").text


# The slowest copy seen on prod (2026-10-30: 4K HEVC, 2 at once, 4 vCPU).
_PROD_COPY_S = 630


def test_a_job_has_time_for_prod_copies_made_one_after_another(
    drive, made, monkeypatch
):
    """Generate on prod was killed at 21 of 26 (its time limit was dev's
    pace): a job may take the slowest prod copy per reel, even in a row."""
    limits: list[int] = []
    monkeypatch.setattr(
        trials_router,
        "_encodes",
        lambda: lambda *_a, job_timeout, **_k: limits.append(job_timeout),
    )
    login = _login(drive, *(f"{i}.mp4" for i in range(26)))
    client = _client()
    client.get(f"/trials/{login}")

    client.post(f"/trials/{login}/generate")
    (reel, *_) = _state(login).reels
    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    assert limits[0] >= 26 * _PROD_COPY_S
    assert limits[1] >= _PROD_COPY_S


def _running_job() -> str:
    """A job row that stays `running` (nothing runs it): a job at work."""
    return create_job("trials", {}, actor=str(users.agency_id()))


def test_a_second_generate_while_one_runs_starts_nothing(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.get(f"/trials/{login}")
    job_id = _running_job()
    trial_state.change(users.agency_id(), login, lambda s: setattr(s, "job_id", job_id))

    client.post(f"/trials/{login}/generate")

    assert made == []
    assert _state(login).job_id == job_id
    page = client.get(f"/trials/{login}").text
    assert f'data-live="/trials/{login}/live"' in page
    assert "data-running" in page
    assert "Making trials… 0 of 1 ready" in page
    (pending,) = _state(login).reels
    live = client.get(f"/trials/{login}/live").json()
    assert live["running"] is True
    assert live["making"] is True
    assert live["status"] == "Making trials… 0 of 1 ready"
    assert live["cards"] == [{"key": f"pending-{pending.key}", "stamp": ""}]
    assert "generating…" in page


def test_fifty_generate_clicks_at_once_start_one_job(drive, made, real_queue):
    """Each click checks and takes the folder in one locked write: the first
    makes the job, the other 49 find it running and do nothing."""
    login = _login(drive, "a.mp4")
    client = _client()
    client.get(f"/trials/{login}")
    real_queue()

    with ThreadPoolExecutor(50) as pool:
        answers = list(
            pool.map(lambda _: client.post(f"/trials/{login}/generate"), range(50))
        )

    assert {r.status_code for r in answers} == {200}
    assert [r.json()["started"] for r in answers].count(True) == 1
    assert get_queue(QueueName.MEDIA).count == 1


def test_a_phone_card_whose_generate_runs_still_opens_its_folder(drive, made):
    """Prod 2026-11-02: Generate clicked, back to /trials, nothing made yet:
    the card had no button left, as if the click was lost."""
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.get(f"/trials/{login}")
    job_id = _running_job()
    trial_state.change(users.agency_id(), login, lambda s: setattr(s, "job_id", job_id))

    page = client.get("/trials").text

    assert page.count(f'href="/trials/{login}"') == 2  # the phone + the button
    assert "Making… 0 of 2" in page
    assert "See generated" not in page


def test_a_caption_applies_while_generate_trials_runs_on_the_others(drive, made):
    """The user fixes a made copy while 27 more are being made: only a reel
    being made itself is blocked."""
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, second = _state(login).reels
    generating = _running_job()
    trial_state.change(
        users.agency_id(), login, lambda s: setattr(s, "job_id", generating)
    )

    page = client.get(f"/trials/{login}").text
    assert page.count("Apply caption") == 2
    client.post(f"/trials/{login}/reel/{first.key}/caption", data={"caption": "mine"})

    assert made[-1] == "mine"
    assert _state(login).job_id == generating  # untouched
    # A reel being made: its buttons wait, the others stay.
    busy = _running_job()
    trial_state.change_reel(
        users.agency_id(), login, second.key, lambda r: setattr(r, "job_id", busy)
    )
    client.post(f"/trials/{login}/reel/{second.key}/regenerate")
    assert len(made) == 3  # no remake of the busy one
    assert made[-1] == "mine"
    page = client.get(f"/trials/{login}").text
    busy_card = page.split(f'id="reel-{second.key}"')[1].split("</section>")[0]
    assert 'class="result-file tr-pending"' in busy_card  # generating, no video
    # Its five captions, Apply caption, Regenerate and Select wait.
    assert busy_card.count(" disabled>") == 8
    free_card = page.split(f'id="reel-{first.key}"')[1].split("</section>")[0]
    assert " disabled>" not in free_card


def test_select_is_kept_at_once_and_upload_takes_it(drive, made):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, _second = _state(login).reels

    r = client.post(
        f"/trials/{login}/reel/{first.key}/select", data={"selected": "true"}
    )

    assert r.json() == {"selected": True}
    assert [x.approved for x in _state(login).reels] == [True, False]
    page = client.get(f"/trials/{login}").text
    assert f'value="{first.key}" form="tr-upload" checked' in page
    client.post(f"/trials/{login}/reel/{first.key}/select", data={"selected": "false"})
    assert not _state(login).reels[0].approved
    assert client.post(f"/trials/{login}/reel/nope/select").status_code == 404


def test_a_reel_the_folder_does_not_have_is_a_404_on_every_reel_route(drive, made):
    """The reel in the path (`Reel`, one dependency for the six routes)."""
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    reel = f"/trials/{login}/reel/nope"

    answers = {
        "regenerate": client.post(f"{reel}/regenerate"),
        "caption": client.post(f"{reel}/caption", data={"caption": "mine"}),
        "captions": client.post(f"{reel}/captions"),
        "select": client.post(f"{reel}/select", data={"selected": "true"}),
        "video": client.get(f"/trials/files/{login}/nope"),
        "poster": client.get(f"/trials/files/{login}/nope/poster"),
    }

    assert {name: r.status_code for name, r in answers.items()} == dict.fromkeys(
        answers, 404
    )
    assert {name: r.json()["detail"] for name, r in answers.items()} == {
        "regenerate": "No such reel",
        "caption": "No such reel",
        "captions": "No such reel",
        "select": "No such reel",
        "video": "Not generated yet",
        "poster": "Not generated yet",
    }
    assert made == ["caption 0"]  # nothing was made for it


def test_a_reel_of_a_login_that_is_not_theirs_is_a_404(drive, made):
    login = _login(drive, "a.mp4")
    _client().post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    stranger = TestClient(app)
    sign_in(stranger)

    answers = [
        stranger.post(f"/trials/{login}/reel/{reel.key}/regenerate"),
        stranger.post(f"/trials/{login}/reel/{reel.key}/select"),
        stranger.get(f"/trials/files/{login}/{reel.key}"),
        stranger.get(f"/trials/files/{login}/{reel.key}/poster"),
    ]

    assert [r.status_code for r in answers] == [404, 404, 404, 404]
    assert len(made) == 1


_CAMERA_NAME = re.compile(r"VID_\d{4}\.mp4")


def test_upload_files_the_selected_copies_under_camera_names_once(drive, made):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, _second = _state(login).reels

    r = client.post(f"/trials/{login}/upload", data={"approved": [first.key]})
    again = client.post(f"/trials/{login}/upload", data={"approved": [first.key]})

    assert r.json() == {"uploading": [first.key]}
    assert again.json() == {"uploading": [first.key]}  # replaced, not added
    (account,) = drive.named("ann.ig - Anna", ROOT)
    (trials,) = drive.named("TRIALS", account)
    (name,) = drive.names_in(trials)
    assert _CAMERA_NAME.fullmatch(name)
    state = _state(login)
    assert [r.status for r in state.reels] == ["uploaded", "generated"]
    assert state.reels[0].uploaded_as == name
    page = client.get(f"/trials/{login}").text
    assert f"In Drive · {name}" in page
    assert state.trials_url is not None
    assert state.trials_url in page


def test_a_second_upload_while_one_runs_starts_nothing(drive, made):
    """Each replaces what is in TRIALS: two at once would delete each other's."""
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, second = _state(login).reels
    running = _running_job()

    def uploading(r: trial_state.ReelState) -> None:
        r.job_id, r.status = running, "uploading"

    trial_state.change_reel(users.agency_id(), login, first.key, uploading)

    r = client.post(
        f"/trials/{login}/upload", data={"approved": [first.key, second.key]}
    )

    assert r.json() == {"uploading": []}
    (account,) = drive.named("ann.ig - Anna", ROOT)
    assert drive.named("TRIALS", account) == []
    assert [x.status for x in _state(login).reels] == ["uploading", "generated"]
    card = client.get(f"/trials/{login}/cards", params={"key": first.key}).text
    assert "uploading…" in card
    assert 'name="approved"' not in card  # nothing to select while it uploads


def test_cards_answers_only_the_cards_asked_for(drive, made):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, second = _state(login).reels

    html = client.get(f"/trials/{login}/cards", params={"key": second.key}).text

    assert f'data-key="{second.key}"' in html
    assert f'data-key="{first.key}"' not in html
    live = client.get(f"/trials/{login}/live").json()
    assert [c["key"] for c in live["cards"]] == [first.key, second.key]
    assert live["running"] is False


def test_dev_reading_the_live_folders_uploads_into_a_twin_in_its_dev_root(
    drive, monkeypatch
):
    """Dev reads prod's POSTED (OFM_DRIVE_READ_LIVE) but writes only in
    "DEV - ALL ACCOUNTS": TRIALS goes into a folder named like hers there;
    her live folder gets nothing and loses nothing. Twice: the scoped Drive of
    a later run finds the folders again, and replaces what the first put there."""
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    drive.seed_folder("ALL ACCOUNTS", MY_DRIVE, item_id="live-root", owner=AGENCY)
    drive.seed_folder("ann.ig - Anna", "live-root", item_id="live-acct", owner=AGENCY)
    drive.seed_folder("TRIALS", "live-acct", item_id="live-trials", owner=AGENCY)
    drive.add_file("live-trials", "VID_1111.mp4", "video/mp4")
    uid = users.agency_id()
    reels = [
        trial_state.ReelState(
            key=key, name=f"{key}.mp4", out=f"out/{key}.mp4", status="generated"
        )
        for key in ("k1", "k2")
    ]
    trial_state.save(
        uid,
        trial_state.FolderState(login_id=7, account_folder_id="live-acct", reels=reels),
    )
    for key in ("k1", "k2"):
        copy = trial_state.work_dir(uid, 7) / "out" / f"{key}.mp4"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(b"copy")

    for key in ("k1", "k2"):
        tasks.upload_trials(
            user_id=uid,
            refresh_token=f"fake-refresh:{AGENCY}",
            login_id=7,
            account_folder_id="live-acct",
            keys=[key],
        )

    (twin,) = drive.named("ann.ig - Anna", ROOT)
    (trials,) = drive.named("TRIALS", twin)
    (name,) = drive.names_in(trials)
    assert _CAMERA_NAME.fullmatch(name)
    assert drive.names_in("live-trials") == ["VID_1111.mp4"]
    assert drive.names_in("live-acct") == ["TRIALS"]
    assert trial_state.load(uid, 7).problem is None


def test_drive_refusing_says_connect_again_and_still_makes_what_is_on_disk(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first = _state(login).reels[0]
    # Its copy is gone, its download is not: made again from disk.
    copy = trial_state.at(users.agency_id(), login, first.out)
    assert copy is not None
    copy.unlink()
    state = _state(login)
    state.reels[0].out = None
    trial_state.save(users.agency_id(), state)
    drive.add_file(_posted(drive), "b.mp4", "video/mp4")
    drive_folders.sync()
    client.get(f"/trials/{login}")  # b.mp4 joins
    drive.refuse_sign_in(AGENCY)

    client.post(f"/trials/{login}/generate")

    state = _state(login)
    assert state.problem == tasks.DRIVE_PROBLEM
    assert made == ["caption 0", "caption 0"]
    assert [r.name for r in state.ready] == ["a.mp4"]
    page = client.get(f"/trials/{login}").text
    assert tasks.DRIVE_PROBLEM in page
    assert "Connect Google Drive" in page


def test_a_copy_that_fails_is_left_out_of_the_page_without_the_error(
    drive, made, monkeypatch
):
    login = _login(drive, "a.mp4")

    def pipeline(src, out, *, caption, seed):
        msg = "ffmpeg exited 234: Invalid argument"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "pipeline_run", pipeline)
    client = _client()
    client.post(f"/trials/{login}/generate")

    assert _state(login).reels[0].error == tasks.MAKE_FAILED  # logged in full
    page = client.get(f"/trials/{login}").text
    assert "Invalid argument" not in page
    assert tasks.MAKE_FAILED not in page
    assert "No trials yet" in page


def test_a_video_gone_from_posted_leaves_unless_its_copy_was_made(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    later = drive.add_file(_posted(drive), "b.mp4", "video/mp4")
    drive_folders.sync()
    client.get(f"/trials/{login}")
    assert len(_state(login).reels) == 2

    drive.delete(later)
    (made_one,) = _state(login).ready
    drive.delete(made_one.key)
    drive_folders.sync()
    client.get(f"/trials/{login}")

    assert [r.name for r in _state(login).reels] == ["a.mp4"]


def _uploaded(client, login: int, *keys: str) -> None:
    client.post(f"/trials/{login}/upload", data={"approved": list(keys)})


def _trials_folder(drive: FakeDrive) -> str:
    (account,) = drive.named("ann.ig - Anna", ROOT)
    (trials,) = drive.named("TRIALS", account)
    return trials


def test_generate_makes_only_what_is_new_in_posted(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    drive.add_file(_posted(drive), "b.mp4", "video/mp4")  # no sweep has read it

    r = client.post(f"/trials/{login}/generate")

    assert r.json() == {"started": True}
    assert made == ["caption 0", "caption 1"]
    assert {x.name: x.made for x in _state(login).reels} == {"a.mp4": 1, "b.mp4": 1}


def test_generate_with_nothing_new_says_so_and_can_be_clicked_again(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    assert client.get(f"/trials/{login}/live").json()["status"] == ""

    client.post(f"/trials/{login}/generate")

    assert made == ["caption 0"]
    assert client.get(f"/trials/{login}/live").json()["status"] == tasks.NOTHING_NEW
    page = client.get(f"/trials/{login}").text
    assert tasks.NOTHING_NEW in page
    generate = page.split('id="tr-generate"')[1].split("</form>")[0]
    assert "disabled" not in generate
    # A new reel next time: the note is gone.
    drive.add_file(_posted(drive), "b.mp4", "video/mp4")
    client.post(f"/trials/{login}/generate")
    assert client.get(f"/trials/{login}/live").json()["status"] == ""


def test_new_reels_that_would_not_download_are_counted_not_called_nothing_new(
    drive, made, monkeypatch
):
    login = _login(drive, "a.mp4", "b.mp4")

    def refused(_self, _file_id):
        msg = "Drive said no"
        raise DriveError(msg)

    monkeypatch.setattr(FakePersonDrive, "download", refused)
    client = _client()

    client.post(f"/trials/{login}/generate")

    assert made == []
    status = client.get(f"/trials/{login}/live").json()["status"]
    assert status == "Could not make 2 new reels. Click Generate trials to try again."


def test_a_new_reel_the_ai_could_not_caption_is_counted_and_made_next_time(
    drive, made, monkeypatch
):
    login = _login(drive, "a.mp4")
    written = tasks.understand

    def down(_src):
        msg = "no model answered"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "understand", down)
    client = _client()
    client.post(f"/trials/{login}/generate")
    status = client.get(f"/trials/{login}/live").json()["status"]
    assert status == "Could not make 1 new reel. Click Generate trials to try again."
    assert "Could not make 1 new reel" in client.get(f"/trials/{login}").text

    monkeypatch.setattr(tasks, "understand", written)
    client.post(f"/trials/{login}/generate")

    assert made == ["caption 0"]
    assert client.get(f"/trials/{login}/live").json()["status"] == ""


def test_posted_unreadable_asks_to_connect_and_never_says_nothing_new(
    drive, made, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    posted = _posted(drive)
    children = FakePersonDrive.children

    def unreadable(self, parent_id, *, folders_only=False):
        if parent_id == posted:
            msg = "Drive said no"
            raise DriveError(msg)
        return children(self, parent_id, folders_only=folders_only)

    monkeypatch.setattr(FakePersonDrive, "children", unreadable)

    client.post(f"/trials/{login}/generate")

    live = client.get(f"/trials/{login}/live").json()
    assert live["status"] == ""
    assert live["problem"] == tasks.DRIVE_PROBLEM
    assert made == ["caption 0"]


def test_the_grid_shows_the_newest_in_posted_first(drive, made):
    login = _login(drive, "old.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    for name in ("middle.mp4", "new.mp4"):
        drive.add_file(_posted(drive), name, "video/mp4")
    client.post(f"/trials/{login}/generate")

    state = _state(login)
    assert [r.name for r in state.ready] == ["new.mp4", "middle.mp4", "old.mp4"]
    keys = [r.key for r in state.ready]
    page = client.get(f"/trials/{login}").text
    assert re.findall(r'id="reel-([^"]+)"', page) == keys
    live = client.get(f"/trials/{login}/live").json()
    assert [c["key"] for c in live["cards"]] == keys


def test_a_folder_made_before_the_order_is_put_in_order_and_a_stale_read_keeps_it():
    """Drive's dates decide. A state from before them comes in any order, and
    the page's last sweep may not name a reel the job has listed since."""

    def video(key: str, day: int) -> DriveFile:
        when = f"2026-11-{day:02d}T10:00:00Z"
        return DriveFile(
            id=key, name=f"{key}.mp4", mimeType="video/mp4", createdTime=when
        )

    made_as = {"out": "out/x.mp4", "status": "generated"}
    state = trial_state.FolderState(
        login_id=1,
        reels=[
            trial_state.ReelState(key="old", name="old.mp4", **made_as),
            trial_state.ReelState(key="new", name="new.mp4", **made_as),
            trial_state.ReelState(key="gone", name="gone.mp4", **made_as),
            trial_state.ReelState(key="mid", name="mid.mp4", **made_as),
        ],
    )

    tasks.merge_posted(state, [video("new", 3), video("mid", 2), video("old", 1)])

    assert [r.key for r in state.reels] == ["new", "mid", "old", "gone"]

    tasks.merge_posted(state, [video("mid", 2), video("old", 1)])  # stale: no "new"

    assert [r.key for r in state.reels] == ["new", "mid", "old", "gone"]

    tasks.merge_posted(state, [video("newer", 4), video("mid", 2), video("old", 1)])

    assert [r.key for r in state.reels] == ["newer", "new", "mid", "old", "gone"]


def test_a_card_offers_five_captions_with_the_one_on_the_copy_marked(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")

    page = client.get(f"/trials/{login}").text

    (reel,) = _state(login).reels
    assert reel.caption == "caption 0"
    assert reel.captions == ["caption 0", *SECOND_TO_FIFTH]
    assert made == ["caption 0"]  # one copy, not five
    assert page.count('name="caption" value="') == 5
    assert page.count('aria-pressed="true"') == 1
    chosen = page.split('aria-pressed="true"')[1].split("</button>")[0]
    assert "caption 0" in chosen
    assert "Get 5 captions" not in page
    assert _form_depth(page) == 1


def test_choosing_a_caption_makes_the_copy_again_with_it_and_keeps_the_choices(
    drive, made
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels

    client.post(
        f"/trials/{login}/reel/{reel.key}/caption", data={"caption": "third one"}
    )

    assert made == ["caption 0", "third one"]
    (again,) = _state(login).reels
    assert again.caption == "third one"
    assert again.captions == ["caption 0", *SECOND_TO_FIFTH]
    chosen = client.get(f"/trials/{login}").text.split('aria-pressed="true"')[1]
    assert "third one" in chosen.split("</button>")[0]


def _made_before_choices(login: int) -> str:
    """Its copy made before there were captions to choose from."""

    def before(state: trial_state.FolderState) -> None:
        state.reels[0].captions = []

    return trial_state.change(users.agency_id(), login, before).reels[0].key


def test_a_reel_made_before_choices_gets_them_and_keeps_its_copy(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    key = _made_before_choices(login)
    page = client.get(f"/trials/{login}").text
    assert f'action="/trials/{login}/reel/{key}/captions"' in page
    assert "Get 5 captions" in page
    stamp = client.get(f"/trials/{login}/live").json()["cards"][0]["stamp"]

    r = client.post(f"/trials/{login}/reel/{key}/captions")

    assert r.json() == {"started": True}
    (reel,) = _state(login).reels
    assert reel.captions == ["caption 1", *SECOND_TO_FIFTH]
    assert reel.caption == "caption 0"  # the copy's, until one is chosen
    assert reel.made == 1
    assert made == ["caption 0"]
    page = client.get(f"/trials/{login}").text
    assert page.count('name="caption" value="') == 5
    assert "Get 5 captions" not in page
    assert client.get(f"/trials/{login}/live").json()["cards"][0]["stamp"] != stamp
    # Has them now: another click asks the AI for nothing.
    again = client.post(f"/trials/{login}/reel/{key}/captions")
    assert again.json() == {"started": False}
    assert client.post(f"/trials/{login}/reel/nope/captions").status_code == 404


def test_captions_the_ai_cannot_write_say_so_and_leave_the_copy(
    drive, made, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    key = _made_before_choices(login)

    def down(_src):
        msg = "no model answered: g1: LLMUnavailableError: secret detail"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "understand", down)

    client.post(f"/trials/{login}/reel/{key}/captions")

    (reel,) = _state(login).reels
    assert reel.error == tasks.CAPTIONS_FAILED
    assert reel.status == "generated"
    assert reel.out
    assert reel.job_id is None
    page = client.get(f"/trials/{login}").text
    assert tasks.CAPTIONS_FAILED in page
    assert "secret detail" not in page


def test_upload_replaces_the_videos_in_trials_with_the_batch(drive, made):
    login = _login(drive, "a.mp4", "b.mp4", "c.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    c, b, a = _state(login).reels
    _uploaded(client, login, a.key, b.key)
    trials = _trials_folder(drive)
    old_ids = {name: drive.named(name, trials)[0] for name in drive.names_in(trials)}
    assert len(old_ids) == 2
    notes = drive.add_file(trials, "notes.txt", "text/plain")

    _uploaded(client, login, b.key, c.key)  # b stays in by going up again

    names = drive.names_in(trials)
    assert len(names) == 3
    assert "notes.txt" in names  # only videos go
    assert drive.item(notes).trashed is False
    assert all(drive.item(i).trashed is True for i in old_ids.values())
    state = _state(login)
    assert state.reel(a.key).status == "generated"
    assert state.reel(a.key).uploaded_as is None
    assert state.reel(b.key).status == "uploaded"
    assert state.reel(c.key).status == "uploaded"
    assert {state.reel(b.key).uploaded_as, state.reel(c.key).uploaded_as} == set(
        names
    ) - {"notes.txt"}
    # Every copy can join the next batch, in Drive or not.
    page = client.get(f"/trials/{login}").text
    assert page.count('name="approved"') == 3
    assert page.count("In Drive · ") == 2


def test_a_batch_that_fails_changes_nothing_in_trials(drive, made, monkeypatch):
    login = _login(drive, "a.mp4", "b.mp4", "c.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    c, b, a = _state(login).reels
    _uploaded(client, login, a.key)
    trials = _trials_folder(drive)
    (kept,) = drive.names_in(trials)
    next_names = iter(["VID_2001.mp4", "VID_2002.mp4"])
    monkeypatch.setattr(tasks, "_camera_name", lambda *_a: next(next_names))
    drive.fail_on("VID_2002.mp4")

    _uploaded(client, login, b.key, c.key)

    assert drive.names_in(trials) == [kept]  # the one that landed taken back
    state = _state(login)
    assert state.reel(a.key).status == "uploaded"
    assert state.reel(a.key).uploaded_as == kept
    for key in (b.key, c.key):
        reel = state.reel(key)
        assert reel.status == "generated"
        assert reel.approved is True  # one click to try again
        assert reel.error == tasks.UPLOAD_FAILED
        assert reel.job_id is None


def test_a_batch_with_drive_down_changes_nothing(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    _uploaded(client, login, reel.key)
    (kept,) = drive.names_in(_trials_folder(drive))
    drive.refuse_sign_in(AGENCY)

    _uploaded(client, login, reel.key)

    (back,) = _state(login).reels
    assert back.status == "uploaded"  # as it was: its old file is still there
    assert back.uploaded_as == kept
    assert back.approved is True
    assert _state(login).problem == tasks.DRIVE_PROBLEM


def test_an_old_trial_that_will_not_delete_says_so(drive, made, monkeypatch):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    b, a = _state(login).reels
    _uploaded(client, login, a.key)

    def refused(_self, _item_id):
        msg = "The user does not have sufficient permissions"
        raise DriveError(msg)

    monkeypatch.setattr(FakePersonDrive, "trash_file", refused)

    _uploaded(client, login, b.key)

    state = _state(login)
    assert state.reel(b.key).status == "uploaded"
    assert state.problem == tasks.DRIVE_PROBLEM


def test_a_reel_made_again_after_upload_is_not_called_in_drive(drive, made):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    _uploaded(client, login, reel.key)

    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    (again,) = _state(login).reels
    assert again.status == "generated"
    assert again.uploaded_as is None
    assert "In Drive · " not in client.get(f"/trials/{login}").text


_HLG_X265 = (
    "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
)


def _hevc_10bit(path):
    """A 1 s 9:16 clip encoded like the snapchat pipeline's copy."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
            "-x265-params", _HLG_X265,
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _probe(path) -> str:
    return subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt,color_transfer,height",
            "-of", "csv=p=0", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


@pytest.mark.heavy
def test_the_page_plays_an_h264_preview_of_the_10bit_hevc_copy(tmp_path):
    copy = _hevc_10bit(tmp_path / "KEY.mp4")
    assert _probe(copy) == "hevc,640,yuv420p10le,arib-std-b67"

    preview = media.preview(copy)
    poster = media.poster(copy)

    assert preview.name == "KEY.preview1920.mp4"
    # SDR again (a browser ignoring HLG shows it washed out), 8-bit H.264.
    assert _probe(preview) == "h264,640,yuv420p,bt709"
    assert poster.read_bytes()[:4] == b"RIFF"
    # Made again only once the copy is newer (a caption applied).
    made_at = preview.stat().st_mtime_ns
    assert media.preview(copy).stat().st_mtime_ns == made_at
    os.utime(copy, ns=(made_at + 10**9, made_at + 10**9))
    assert media.preview(copy).stat().st_mtime_ns > made_at


@pytest.mark.heavy
def test_the_ai_watches_a_small_proxy_with_its_sound(tmp_path):
    reel = tmp_path / "KEY-VID_1.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(reel),
        ],
        check=True,
    )  # fmt: skip

    proxy = media.ai_proxy(reel)

    assert proxy.name == "KEY-VID_1.ai.mp4"
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,height",
         "-of", "csv=p=0", str(proxy)],
        check=True, capture_output=True, text=True,
    ).stdout.split()  # fmt: skip
    assert sorted(streams) == ["audio", "video,640"]
    assert proxy.stat().st_size < reel.stat().st_size
    assert media.ai_proxy(reel) == proxy  # made once


@pytest.fixture
def real_queue(monkeypatch):
    """Jobs wait in Redis from here on, instead of running inline."""

    def switch_on() -> None:
        monkeypatch.setenv("OFM_RQ_ASYNC", "true")
        get_redis().flushdb()

    yield switch_on
    get_redis().flushdb()


def test_copies_are_made_one_after_another(drive, made, monkeypatch):
    """Prod 2026-11-04: 3 folders x 2 reels at once = 6 encodes of 1.5 GB on
    7.75 GB, the kernel killing one every 30 s and nothing finishing."""
    login = _login(drive, "a.mp4", "b.mp4", "c.mp4")
    at_once = most = 0

    def pipeline(src, out, *, caption, seed):
        nonlocal at_once, most
        at_once += 1
        most = max(most, at_once)
        time.sleep(0.02)
        out.write_bytes(b"copy")
        at_once -= 1

    monkeypatch.setattr(tasks, "pipeline_run", pipeline)

    _client().post(f"/trials/{login}/generate")

    assert len(_state(login).ready) == 3
    assert most == 1


def test_generate_waits_its_turn_and_the_pages_say_so(drive, made, real_queue):
    login = _login(drive, "a.mp4")
    client = _client()
    real_queue()
    JobQueue(QueueName.MEDIA).put(worker_tasks.note_down, "before")

    client.post(f"/trials/{login}/generate")

    assert made == []
    assert get_queue(QueueName.MEDIA).count == 2
    card = client.get("/trials").text
    assert "In queue · 1 ahead" in card
    assert "Making… 0 of 1" not in card
    page = client.get(f"/trials/{login}").text
    assert "In queue · 1 ahead" in page
    assert "in queue…" in page
    assert "generating…" not in page
    live = client.get(f"/trials/{login}/live").json()
    assert live["status"] == "In queue · 1 ahead"
    (pending,) = _state(login).reels
    assert live["cards"] == [{"key": f"pending-{pending.key}", "stamp": "queued"}]


def test_the_first_in_line_with_nothing_running_is_next(drive, made, real_queue):
    login = _login(drive, "a.mp4")
    client = _client()
    real_queue()

    client.post(f"/trials/{login}/generate")

    assert client.get(f"/trials/{login}/live").json()["status"] == "In queue · next"


def test_regenerate_waits_in_the_same_line(drive, made, real_queue):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).ready
    real_queue()

    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    assert get_queue(QueueName.MEDIA).count == 1
    page = client.get(f"/trials/{login}").text
    assert "in queue…" in page
    assert client.get(f"/trials/{login}/live").json()["status"] == "In queue · next"


def test_the_status_line_counts_what_failed_while_the_run_goes_on(
    drive, made, monkeypatch
):
    """Prod 2026-11-04: reels killed one by one and the page just stayed empty."""
    login = _login(drive, "a.mp4", "b.mp4")

    def pipeline(src, out, *, caption, seed):
        msg = "ffmpeg failed: -9"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "pipeline_run", pipeline)
    client = _client()
    client.post(f"/trials/{login}/generate")
    job_id = _running_job()
    trial_state.change(users.agency_id(), login, lambda s: setattr(s, "job_id", job_id))

    status = client.get(f"/trials/{login}/live").json()["status"]

    assert status == "Making trials… 0 of 2 ready, 2 failed"


def test_a_new_generate_takes_the_failed_reels_again(
    drive, made, monkeypatch, real_queue
):
    login = _login(drive, "a.mp4")

    def pipeline(src, out, *, caption, seed):
        msg = "ffmpeg failed: -9"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "pipeline_run", pipeline)
    client = _client()
    client.post(f"/trials/{login}/generate")
    assert len(_state(login).failed) == 1
    real_queue()

    client.post(f"/trials/{login}/generate")

    assert _state(login).failed == []
    (pending,) = _state(login).reels
    live = client.get(f"/trials/{login}/live").json()
    assert live["cards"] == [{"key": f"pending-{pending.key}", "stamp": "queued"}]


# --- a Generate cut off by a deploy ------------------------------------------


@pytest.fixture
def nobody_holds(monkeypatch):
    """Recovery's view: no worker or queue holds any job, no grace."""
    monkeypatch.setattr(recovery, "active_job_ids", set)
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")


def _cut_off(login: int, label: str = "Making trials", **params) -> str:
    """A Generate a deploy killed: its row still running, nobody on it."""
    job_id = create_job(
        "trials",
        {"label": label, "login": login, **params},
        actor=str(users.agency_id()),
    )
    trial_state.change(users.agency_id(), login, lambda s: setattr(s, "job_id", job_id))
    return job_id


@pytest.mark.usefixtures("nobody_holds")
def test_a_generate_cut_off_by_a_deploy_goes_on_where_it_stopped(drive, made):
    """Prod 2026-11-07: a deploy cut a Generate at 52 min (workers get 120 s,
    one runs for hours) and it failed "Interrupted by a restart". Recovery
    now queues it again; copies already made are left alone."""
    login = _login(drive, "a.mp4")
    _client().post(f"/trials/{login}/generate")
    drive.add_file(_posted(drive), "b.mp4", "video/mp4")
    job_id = _cut_off(login)

    assert recovery.run_recovery_once().entries == {job_id: "restarted"}

    assert made == ["caption 0", "caption 1"]  # a.mp4 not made again
    job = get_job(job_id)
    assert job.status == "done"
    assert job.params["restarts"] == 1
    assert len(_state(login).ready) == 2


@pytest.mark.usefixtures("nobody_holds")
def test_a_restarted_generate_waits_on_the_media_queue_with_its_time_limit(
    drive, made, real_queue
):
    login = _login(drive, "a.mp4", "b.mp4")
    _client().get(f"/trials/{login}")  # the page knows both reels
    job_id = _cut_off(login)
    real_queue()

    assert recovery.run_recovery_once().entries == {job_id: "restarted"}

    (queued,) = get_queue(QueueName.MEDIA).get_jobs()
    assert queued.args[0] == job_id
    assert queued.timeout >= 2 * _PROD_COPY_S
    assert made == []


@pytest.mark.usefixtures("nobody_holds")
@pytest.mark.parametrize(
    ("label", "params"),
    [("Making trials", {"restarts": 2}), ("Making one trial", {})],
    ids=["cut off twice already", "one reel's remake"],
)
def test_a_job_that_cannot_start_over_still_fails_as_interrupted(
    drive, made, label, params
):
    login = _login(drive, "a.mp4")
    job_id = _cut_off(login, label, **params)

    assert recovery.run_recovery_once().entries == {job_id: "interrupted"}

    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == recovery.INTERRUPTED
    assert made == []


# --- a day old: the video goes, the poster and the recipe stay ----------------

_TWO_DAYS_S = 2 * 86400


@pytest.fixture
def posters(made, monkeypatch) -> None:
    """A poster is a file of its own next to the copy (the cleanup keeps it)."""

    def poster(copy, *, again=False):
        dest = media.poster_path(copy)
        dest.write_bytes(b"RIFF")
        return dest

    monkeypatch.setattr(media, "poster", poster)


def _a_day_later(login: int) -> None:
    cleanup.clean(users.agency_id(), login, time.time() + _TWO_DAYS_S)


def _files(login: int) -> list[str]:
    root = trial_state.work_dir(users.agency_id(), login)
    return sorted(p.name for p in root.rglob("*") if p.is_file())


def test_a_copy_a_day_old_keeps_only_its_poster_and_what_made_it(drive, made, posters):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (before,) = _state(login).reels

    _a_day_later(login)

    (reel,) = _state(login).reels
    assert _files(login) == [f"{reel.key}.poster.webp", "state.json"]
    assert (reel.stored, reel.local, reel.status) == (False, None, "generated")
    assert reel.recipe == trial_state.Recipe(
        seed=tasks._seed(reel.key, 0),
        caption="caption 0",
        pipeline="snapchat_iphone16_usa",
    )
    assert reel.recipe == before.recipe
    page = client.get(f"/trials/{login}").text
    src = f"/trials/files/{login}/{reel.key}"
    assert f'<img class="tr-video" src="{src}/poster?v=1"' in page
    assert "<video" not in page
    assert f'name="approved" value="{reel.key}"' in page  # still selectable
    assert client.get(f"{src}/poster").content == b"RIFF"
    assert client.get(src).status_code == 404


def test_a_copy_made_today_or_being_worked_on_is_left(drive, made, posters):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    uid = users.agency_id()
    first, second = _state(login).reels

    cleanup.clean(uid, login, time.time())  # today

    assert all(r.stored for r in _state(login).reels)
    job = _running_job()
    trial_state.change_reel(uid, login, first.key, lambda r: r.claim_remake(job))

    _a_day_later(login)

    state = _state(login)
    assert [r.stored for r in state.reels] == [True, False]
    assert f"{first.key}.mp4" in _files(login)
    assert f"{second.key}.mp4" not in _files(login)


def test_uploading_a_deleted_copy_makes_the_same_one_again_first(
    drive, made, posters, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()
    runs: list[tuple[str, int]] = []
    pipeline = tasks.pipeline_run

    def recording(src, out, *, caption, seed):
        assert src.is_file()  # downloaded again
        runs.append((caption, seed))
        pipeline(src, out, caption=caption, seed=seed)

    monkeypatch.setattr(tasks, "pipeline_run", recording)
    client.post(f"/trials/{login}/generate")
    _a_day_later(login)
    (reel,) = _state(login).reels

    r = client.post(f"/trials/{login}/upload", data={"approved": [reel.key]})

    assert r.json() == {"uploading": [reel.key]}
    assert runs[0] == runs[1]  # the same caption and seed: the same edit
    (again,) = _state(login).reels
    assert (again.status, again.stored, again.made) == ("uploaded", True, 1)
    assert drive.names_in(_trials_folder(drive)) == [again.uploaded_as]
    assert "<video" in client.get(f"/trials/{login}").text


def test_an_upload_that_makes_a_copy_again_waits_its_turn_to_encode(
    drive, made, posters, real_queue
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    _a_day_later(login)
    real_queue()

    client.post(f"/trials/{login}/upload", data={"approved": [reel.key]})

    assert get_queue(QueueName.MEDIA).count == 1


def test_an_upload_of_copies_on_disk_does_not_wait_behind_encodes(
    drive, made, posters, real_queue
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    real_queue()

    client.post(f"/trials/{login}/upload", data={"approved": [reel.key]})

    assert get_queue(QueueName.MEDIA).count == 0


def test_regenerate_and_a_caption_work_on_a_deleted_copy(drive, made, posters):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    _a_day_later(login)
    (reel,) = _state(login).reels

    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    (again,) = _state(login).reels
    assert (again.stored, again.made, again.status) == (True, 2, "generated")
    assert again.recipe is not None
    assert again.recipe.seed == tasks._seed(reel.key, 1)  # a new variant
    _a_day_later(login)

    client.post(f"/trials/{login}/reel/{reel.key}/caption", data={"caption": "mine"})

    (mine,) = _state(login).reels
    assert (mine.stored, mine.made, mine.caption) == (True, 3, "mine")
    assert made == ["caption 0", "caption 0", "mine"]


def test_a_deleted_copy_whose_source_left_drive_says_so_and_the_rest_go_up(
    drive, made, posters
):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    _a_day_later(login)
    gone, kept = _state(login).reels
    drive.delete(gone.key)

    _uploaded(client, login, gone.key, kept.key)

    first, second = _state(login).reels
    assert (first.status, first.error, first.approved) == (
        "generated",
        "Source video is gone from Drive",
        True,
    )
    assert (second.status, second.stored) == ("uploaded", True)
    assert drive.names_in(_trials_folder(drive)) == [second.uploaded_as]


def test_a_copy_from_before_recipes_were_kept_is_made_again_the_same(
    drive, made, posters, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    _a_day_later(login)
    uid = users.agency_id()
    (reel,) = _state(login).reels

    def forget(r: trial_state.ReelState) -> None:
        r.recipe = None

    trial_state.change_reel(uid, login, reel.key, forget)
    seeds: list[int] = []
    pipeline = tasks.pipeline_run

    def recording(src, out, *, caption, seed):
        seeds.append(seed)
        pipeline(src, out, caption=caption, seed=seed)

    monkeypatch.setattr(tasks, "pipeline_run", recording)

    _uploaded(client, login, reel.key)

    assert seeds == [tasks._seed(reel.key, 0)]
    assert made[-1] == "caption 0"
    assert _state(login).reels[0].status == "uploaded"


def test_a_source_never_made_into_a_copy_goes_after_a_day_and_comes_back(
    drive, made, posters, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()

    def broken(src, out, *, caption, seed):
        msg = "encoder died"
        raise RuntimeError(msg)

    working = tasks.pipeline_run
    monkeypatch.setattr(tasks, "pipeline_run", broken)
    client.post(f"/trials/{login}/generate")
    assert any(name.endswith("a.mp4") for name in _files(login))

    _a_day_later(login)

    assert _files(login) == ["state.json"]
    monkeypatch.setattr(tasks, "pipeline_run", working)
    client.post(f"/trials/{login}/generate")
    (reel,) = _state(login).reels
    assert (reel.status, reel.stored) == ("generated", True)


def test_the_sweep_cleans_every_folder_and_a_broken_one_stops_nothing(
    drive, made, posters, monkeypatch, tmp_path
):
    login = _login(drive, "a.mp4")
    _client().post(f"/trials/{login}/generate")
    broken = tmp_path / "trials" / "999" / "1"
    broken.mkdir(parents=True)
    (broken / "state.json").write_text("not json", encoding="utf-8")
    (reel,) = _state(login).reels
    copy = trial_state.at(users.agency_id(), login, reel.out)
    assert copy is not None
    old = time.time() - _TWO_DAYS_S
    os.utime(copy, (old, old))

    cleanup.remove_old()

    assert _state(login).reels[0].stored is False
    assert not copy.exists()


@pytest.mark.heavy
def test_the_poster_is_a_frame_from_the_middle(tmp_path):
    copy = _hevc_10bit(tmp_path / "KEY.mp4")
    first = tmp_path / "first.webp"
    write_video_thumb(copy, first, 480)

    poster = media.poster(copy)

    assert poster.read_bytes()[:4] == b"RIFF"
    assert poster.read_bytes() != first.read_bytes()


def test_the_cleanup_sweep_is_put_in_the_workers_line_once(real_queue):
    """Its ffmpeg and file work is a worker's, never the web process's; a
    round while the last one still waits (a busy worker) adds nothing."""
    (sweep,) = [s for s in sweeps.sweeps() if s.name == "trials cleanup"]
    real_queue()

    sweep.round()
    sweep.round()

    queue = get_queue(QueueName.PREVIEWS)
    assert queue.get_job_ids() == ["sweep-trials-cleanup"]
    # Done (a finished job is not kept): the next round puts it again.
    queue.empty()
    sweep.round()
    assert queue.count == 1


class _Ran:
    """What the pipeline answers: `repurpose.RunResult`, as far as trials reads it."""

    seconds = 9.5

    class step:  # noqa: N801 - the attribute's name
        @staticmethod
        def model_dump(**kwargs):
            assert kwargs["exclude"] == {"command"}
            return {"video": {"speed": 1.03}, "audio": {"pitch": 0.98}}


def test_what_the_pipeline_drew_is_kept_with_the_recipe_and_survives_a_restore(
    drive, made, posters, monkeypatch
):
    login = _login(drive, "a.mp4")
    client = _client()
    pipeline = tasks.pipeline_run

    def drawing(src, out, *, caption, seed):
        pipeline(src, out, caption=caption, seed=seed)
        return _Ran()

    monkeypatch.setattr(tasks, "pipeline_run", drawing)
    client.post(f"/trials/{login}/generate")
    drew = {"video": {"speed": 1.03}, "audio": {"pitch": 0.98}, "seconds": 9.5}
    (reel,) = _state(login).reels
    assert reel.recipe is not None
    assert reel.recipe.draws == drew

    _a_day_later(login)
    _uploaded(client, login, reel.key)

    (again,) = _state(login).reels
    assert again.status == "uploaded"
    assert again.recipe == reel.recipe  # the copy's own, not the re-render's


def test_a_copy_whose_source_left_posted_is_never_deleted(drive, made, posters):
    """It could not be made again: the copy is all there is of it."""
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    gone, _kept = _state(login).reels
    drive.delete(gone.key)
    drive_folders.sync()  # the read the cleanup goes by

    _a_day_later(login)

    first, second = _state(login).reels
    assert (first.key, first.stored, first.posted) == (gone.key, True, False)
    assert (second.stored, second.posted) == (False, True)
    assert f"{gone.key}.mp4" in _files(login)


def test_a_folder_nobody_read_since_the_update_keeps_its_copies(drive, made, posters):
    """A state file from before `posted` was kept: unknown is not "in Drive"."""
    login = _login(drive, "a.mp4")
    _client().post(f"/trials/{login}/generate")
    uid = users.agency_id()

    def unknown(state: trial_state.FolderState) -> None:
        state.posted_folder_id = ""
        for reel in state.reels:
            reel.posted = None

    trial_state.change(uid, login, unknown)
    devices.remove_login(devices.list_devices()[0].id, login)  # no card any more

    _a_day_later(login)

    assert _state(login).reels[0].stored is True
