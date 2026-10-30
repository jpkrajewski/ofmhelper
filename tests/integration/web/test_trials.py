"""/trials: /drive's phone cards; Generate trials downloads a login's POSTED
videos and makes a captioned copy of each not made yet (one job per folder
at a time); the phone opens the folder: every copy playable (an H.264
preview of the HEVC copy, a poster), its caption to fix or renew, a tick to
upload; Upload files the ticked ones once into TRIALS next to POSTED. What
went wrong is said in short words, never the exception."""

import os
import re
import subprocess
from html.parser import HTMLParser

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.trials import media, tasks
from ofmhelpers.trials import state as trial_state
from ofmhelpers.trials.captions import ReelNotes
from ofmhelpers.web import drive_folders
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields, LoginFields
from ofmhelpers.web.stores import devices, users
from ofmhelpers.web.stores.jobs import create_job

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


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
        tasks, "caption_for", lambda notes, used: f"caption {next(asked)}"
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
    return added[0]


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
    monkeypatch.setattr(tasks, "caption_for", no_ai)

    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")
    client.post(f"/trials/{login}/reel/{reel.key}/regenerate")

    (again,) = _state(login).reels
    assert again.caption == "caption 0"
    assert made == ["caption 0"] * 3
    assert again.made == 3
    assert len(set(seeds)) == 2  # a new variant each time
    assert '?v=3"' in client.get(f"/trials/{login}").text


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
    # Apply caption, Regenerate and Select wait.
    assert busy_card.count(" disabled>") == 3
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


_CAMERA_NAME = re.compile(r"VID_\d{4}\.mp4")


def test_upload_files_the_selected_copies_once_under_camera_names(drive, made):
    login = _login(drive, "a.mp4", "b.mp4")
    client = _client()
    client.post(f"/trials/{login}/generate")
    first, _second = _state(login).reels

    r = client.post(f"/trials/{login}/upload", data={"approved": [first.key]})
    again = client.post(f"/trials/{login}/upload", data={"approved": [first.key]})

    assert r.json() == {"uploading": [first.key]}
    assert again.json() == {"uploading": []}  # already in Drive
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


def test_a_second_upload_while_one_runs_takes_only_the_other_reels(drive, made):
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

    assert r.json() == {"uploading": [second.key]}
    (account,) = drive.named("ann.ig - Anna", ROOT)
    (trials,) = drive.named("TRIALS", account)
    assert len(drive.names_in(trials)) == 1
    assert [x.status for x in _state(login).reels] == ["uploading", "uploaded"]
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
    her live folder gets nothing. Twice: the scoped Drive of a later run
    finds the folders again."""
    monkeypatch.setenv("OFM_DRIVE_READ_LIVE", "1")
    monkeypatch.setenv("OFM_ENV", "development")
    drive.seed_folder("ALL ACCOUNTS", MY_DRIVE, item_id="live-root", owner=AGENCY)
    drive.seed_folder("ann.ig - Anna", "live-root", item_id="live-acct", owner=AGENCY)
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
    names = drive.names_in(trials)
    assert len(set(names)) == 2
    assert all(_CAMERA_NAME.fullmatch(n) for n in names)
    assert drive.names_in("live-acct") == []
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
