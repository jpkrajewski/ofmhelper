import re
import time
lazy from pathlib import Path

import pytest
import time_machine
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.drive_folders import store as folder_store
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import create_job, history
from ofmhelpers.db.tracked_accounts.store import list_accounts, record_scan
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role
from ofmhelpers.db.users.store import _repository as user_repository
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.drive import uploads as drive_uploads
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.gdrive import DriveError, DriveFile
from ofmhelpers.integrations.gdrive.fake import fake_drive
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.socials import AccountProfile, AccountStats
from ofmhelpers.web.main import app

ROOT = "upload-root-folder"
PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_GDRIVE_SERVICE_ACCOUNT", "{}")
    monkeypatch.setenv("OFM_GDRIVE_ROOT_FOLDER", ROOT)
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return fake_drive()


def _client(email: str, *, google: bool = True) -> TestClient:
    c = TestClient(app)
    user = sign_in(c, email)
    if google:
        user_repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return c


def _account_folder(client: TestClient, handle: str = "ann.ig") -> int:
    phone = client.post("/phones/add", json=PHONE).json()["id"]
    login = client.post(
        f"/phones/{phone}/logins", json={"service": "instagram", "username": handle}
    ).json()["id"]
    drive_folders.sync()
    return login


def _finished_run(email: str, tmp_path: Path, name: str = "clone.mp4") -> str:
    video = tmp_path / name
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    params = {
        "prompt": "p",
        "resolution": "720P",
        "audio": False,
        "duration": 5,
        "reference_images": [],
        "reference_videos": [str(video)],
        "clone_batch": f"batch-{name}",
        "clone_index": 0,
    }
    job_id = create_job("clone", params, actor=str(users.by_email(email).id))
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})
    return job_id


def _root_of(email: str) -> str:
    root = users.by_email(email).drive_root_id
    assert root, f"{email} has no Drive root yet"
    return root


def _files_in(drive, folder_name: str, email: str = "ann@example.com") -> list[str]:
    (folder,) = drive.named(folder_name, _root_of(email))
    return [n for n in drive.names_in(folder) if n != "POSTED"]


def test_a_run_uploads_into_the_account_folder_once(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    assert r.json()["queued"] in (0, 1)
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert state["folder_name"] == "ann.ig - Anna"
    assert state["drive_url"]
    [uploaded] = _files_in(drive, "ann.ig - Anna")
    assert uploaded.startswith("VID_")
    (folder,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (file_id,) = drive.named(uploaded, folder)
    assert drive.item(file_id).owner == "ann@example.com"

    again = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]}
    )
    assert again.json()["queued"] == 0
    assert len(_files_in(drive, "ann.ig - Anna")) == 1
    assert "In Drive:" in ann.get("/clone").text


def test_without_google_drive_connected_nothing_is_queued(drive, tmp_path):
    ann = _client("ann@example.com", google=False)
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    assert r.status_code == 400
    assert "Connect Google Drive" in r.json()["detail"]
    assert users.by_email("ann@example.com").drive_root_id is None


def test_nobody_uploads_into_anothers_folder_or_anothers_run(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    ann_login = _account_folder(ann)
    ann_job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    bob_job = _finished_run("bob@example.com", tmp_path, "b.mp4")

    r = bob.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [ann_login]}
    )
    assert r.status_code == 400
    r = ann.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [ann_login]}
    )
    assert r.json()["uploads"] == []
    assert bob.get("/clone/drive-uploads", params={"job": ann_job}).json() == []
    assert _files_in(drive, "ann.ig - Anna") == []


def test_a_failed_upload_says_why_and_can_be_retried(drive, tmp_path, monkeypatch):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    working = drive_uploads.connect_as

    def refused(_token):
        msg = "Uploading: Drive refused (quota)"
        raise DriveError(msg)

    monkeypatch.setattr(drive_uploads, "connect_as", refused)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Uploading: Drive refused (quota)",
    )
    assert "Drive upload to ann.ig - Anna failed" in ann.get("/clone").text

    monkeypatch.setattr(drive_uploads, "connect_as", working)
    retry = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]}
    )
    assert retry.status_code == 200
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"


def test_the_drive_picker_offers_only_the_users_folders(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    login = _account_folder(ann)
    _finished_run("ann@example.com", tmp_path, "a.mp4")
    _finished_run("bob@example.com", tmp_path, "b.mp4")

    ann_page = ann.get("/clone").text
    assert "Upload to Drive" in ann_page
    assert f'data-login="{login}"' in ann.get("/drive").text
    bob_page = bob.get("/clone").text
    assert "ann.ig - Anna" not in bob_page
    assert 'id="drive-picker"' not in bob_page
    assert "Upload to Drive" not in bob_page


def test_queueing_into_several_folders_at_once(drive, tmp_path):
    ann = _client("ann@example.com")
    first = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    r = ann.post(
        "/clone/drive-upload", json={"job_ids": [job], "login_ids": [first, 999]}
    )
    assert r.status_code == 400
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [first]})
    assert [u["status"] for u in r.json()["uploads"]] == ["done"]


def test_queueing_returns_only_the_users_own_runs(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    ann_login = _account_folder(ann)
    bob_login = _account_folder(bob, "bob.ig")
    ann_job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    bob_job = _finished_run("bob@example.com", tmp_path, "b.mp4")
    bob.post(
        "/clone/drive-upload", json={"job_ids": [bob_job], "login_ids": [bob_login]}
    )

    r = ann.post(
        "/clone/drive-upload",
        json={"job_ids": [ann_job, bob_job], "login_ids": [ann_login]},
    )

    assert r.status_code == 200, r.text
    assert [u["job_id"] for u in r.json()["uploads"]] == [ann_job]


def test_an_upload_that_cannot_be_queued_fails_and_can_be_retried(
    drive, tmp_path, monkeypatch
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    working = drive_uploads.enqueue

    def redis_down(*_args, **_kwargs):
        msg = "redis down"
        raise ConnectionError(msg)

    monkeypatch.setattr(drive_uploads, "enqueue", redis_down)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Could not queue the upload: redis down",
    )

    monkeypatch.setattr(drive_uploads, "enqueue", working)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"


def test_a_stranded_queued_upload_is_queued_again_once_stale(
    drive, tmp_path, monkeypatch
):
    monkeypatch.setenv("OFM_GDRIVE_UPLOAD_STALE_S", "60")
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    working = drive_uploads.enqueue
    monkeypatch.setattr(drive_uploads, "enqueue", lambda *_a, **_k: None)
    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    monkeypatch.setattr(drive_uploads, "enqueue", working)

    ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "queued"
    assert _files_in(drive, "ann.ig - Anna") == []

    with time_machine.travel(time.time() + 120):
        ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
        [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert len(_files_in(drive, "ann.ig - Anna")) == 1


def test_any_error_uploading_a_file_fails_that_upload(drive, tmp_path, monkeypatch):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)

    def vanished(*_args, **_kwargs):
        msg = "clone.mp4 is gone"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(drive, "upload", vanished)
    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        "Upload failed: clone.mp4 is gone",
    )


def test_dev_never_uploads_into_a_live_root_kept_from_a_prod_copy(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    ann_id = users.by_email("ann@example.com").id
    live = drive.seed_folder(
        "ALL ACCOUNTS", "mydrive-ann@example.com", owner="ann@example.com"
    )
    live_account = drive.seed_folder("ann.ig - Anna", live, owner="ann@example.com")
    folder_store.save_account_folder(login, live_account, "ann.ig - Anna", live)
    users.set_drive_root(ann_id, live)
    drive.clear_calls()

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert (state["status"], state["error"]) == (
        "failed",
        (
            "The account's folder is not in its owner's DEV - ALL ACCOUNTS folder:"
            " Sync on /drive, then upload again"
        ),
    )
    assert drive.names_in(live_account) == []
    assert drive.writes == []


def test_clone_and_drive_offer_connecting_google_drive_until_it_is(drive):
    connect = "/auth/google/start?link=1&amp;next="
    ann = _client("ann@example.com", google=False)
    assert f"{connect}/clone" in ann.get("/clone").text
    assert f"{connect}/drive" in ann.get("/drive/live").text

    connected = _client("bob@example.com")
    assert connect not in connected.get("/clone").text
    assert connect not in connected.get("/drive/live").text


def _file(name: str) -> DriveFile:
    return DriveFile(id=name, name=f"{name}.mp4", mime_type="video/mp4")


def _upload(client: TestClient, job: str, login: int) -> dict:
    client.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})
    [state] = client.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    return state


def test_a_video_put_in_the_wrong_folder_is_taken_back_out_from_the_card(
    drive, tmp_path
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    state = _upload(ann, job, login)
    (folder,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (file_id,) = drive.named(_files_in(drive, "ann.ig - Anna")[0], folder)
    page = ann.get("/clone").text
    assert 'class="icon-btn drive-remove"' in page
    assert f'data-upload="{state["id"]}"' in page

    r = ann.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "removed"
    assert drive.item(file_id).trashed
    assert _files_in(drive, "ann.ig - Anna") == []
    assert "In Drive:" not in ann.get("/clone").text
    assert _upload(ann, job, login)["status"] == "done"


def test_nobody_removes_anothers_upload(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    login = _account_folder(ann)
    state = _upload(ann, _finished_run("ann@example.com", tmp_path), login)

    r = bob.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert r.status_code == 400
    assert len(_files_in(drive, "ann.ig - Anna")) == 1


def test_an_upload_already_gone_in_drive_is_removed_quietly(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    state = _upload(ann, _finished_run("ann@example.com", tmp_path), login)
    (folder,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (file_id,) = drive.named(_files_in(drive, "ann.ig - Anna")[0], folder)
    drive.delete(file_id)

    r = ann.post(f"/clone/drive-uploads/{state['id']}/remove")
    assert (r.status_code, r.json()["status"]) == (200, "removed")


def test_a_revoked_google_token_refuses_the_remove_with_a_reason(
    drive, tmp_path, monkeypatch
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    state = _upload(ann, _finished_run("ann@example.com", tmp_path), login)

    def revoked(_token):
        msg = "Token has been expired or revoked"
        raise SignInError(msg)

    monkeypatch.setattr(drive_uploads, "connect_as", revoked)

    r = ann.post(f"/clone/drive-uploads/{state['id']}/remove")
    assert r.status_code == 400
    assert r.json()["detail"] == "Connect Google Drive on your Account page first"


def test_an_upload_leaves_a_job_row_naming_what_went_up(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    state = _upload(ann, _finished_run("ann@example.com", tmp_path), login)

    [row] = [job for job in history() if job.task == "drive_upload"]
    assert row.actor == str(users.by_email("ann@example.com").id)
    assert (row.status, row.params) == ("done", {"upload_ids": [state["id"]]})


def test_drive_failing_the_remove_is_a_502_in_our_words_and_keeps_the_upload(
    drive, tmp_path, monkeypatch
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_run("ann@example.com", tmp_path)
    state = _upload(ann, job, login)

    class Failing:
        def trash_file(self, _file_id):
            msg = "backendError 500: internal-google-detail"
            raise DriveError(msg)

    monkeypatch.setattr(drive_uploads, "connect_as", lambda _token: Failing())

    r = ann.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert r.status_code == 502
    assert r.json() == {"detail": "Google Drive did not remove it. Try again."}
    assert "internal-google-detail" not in r.text
    [kept] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert kept["status"] == "done"
    assert len(_files_in(drive, "ann.ig - Anna")) == 1


def test_deleted_in_drive_stops_showing_in_drive_moved_to_posted_does_not(
    drive, tmp_path
):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    posted_job = _finished_run("ann@example.com", tmp_path, "a.mp4")
    deleted_job = _finished_run("ann@example.com", tmp_path, "b.mp4")
    _upload(ann, posted_job, login)
    _upload(ann, deleted_job, login)
    (folder,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (posted,) = drive.named("POSTED", folder)
    first, second = sorted(
        (drive.item(i).name, i)
        for name in _files_in(drive, "ann.ig - Anna")
        for i in drive.named(name, folder)
    )
    by_job = {
        s["job_id"]: s
        for s in ann.get(
            "/clone/drive-uploads", params={"job": [posted_job, deleted_job]}
        ).json()
    }
    file_of = {
        j: next(i for _n, i in (first, second) if i in by_job[j]["drive_url"])
        for j in (posted_job, deleted_job)
    }
    drive.move(file_of[posted_job], posted)
    drive.trash(file_of[deleted_job])

    with time_machine.travel(time.time() + 5):
        drive_folders.sync()

    states = {
        s["job_id"]: s["status"]
        for s in ann.get(
            "/clone/drive-uploads", params={"job": [posted_job, deleted_job]}
        ).json()
    }
    assert states == {posted_job: "done", deleted_job: "removed"}


def test_upload_to_drive_opens_drive_with_the_video_and_followers(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    owner = users.by_email("ann@example.com").id
    (account,) = [a for a in list_accounts(owner) if a.handle == "ann.ig"]
    record_scan(
        account.id,
        AccountStats(
            profile=AccountProfile(handle="ann.ig", followers=12300),
            posts=[],
            scanned_at=time.time(),
        ),
    )
    job = _finished_run("ann@example.com", tmp_path)

    page = ann.get("/drive", params={"upload": job}).text

    assert f'data-job="{job}"' in page
    assert "<h2>Click phone to upload reel there</h2>" in page
    assert 'data-name="VID_' in page
    (src,) = re.findall(r'<video src="([^"]+)"', page)
    assert src.startswith(f"/clone/files/{job}/0")
    played = ann.get(src)
    assert played.status_code == 200
    assert played.headers["content-type"].startswith("video/")
    assert 'data-already=""' in page
    assert '@ann.ig <span class="dr-followers">· 12.3k followers</span>' in page
    assert f'data-login="{login}"' in page
    assert 'data-folder="ann.ig - Anna"' in page

    _upload(ann, job, login)
    page = ann.get("/drive", params={"upload": job}).text
    assert f'data-already="{login}"' in page


def test_upload_mode_only_for_the_sessions_own_finished_run(drive, tmp_path):
    ann = _client("ann@example.com")
    _client("bob@example.com")
    _account_folder(ann)
    bob_job = _finished_run("bob@example.com", tmp_path, "b.mp4")
    assert 'id="dr-pick"' not in ann.get("/drive", params={"upload": bob_job}).text
    assert 'id="dr-pick"' not in ann.get("/drive", params={"upload": "nope"}).text
    assert 'id="dr-pick"' not in ann.get("/drive").text


def test_an_owner_posts_their_vas_clone_and_the_va_cannot(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    owner = users.by_email("ann@example.com")
    va = TestClient(app)
    va_user = sign_in(va, "va@example.com", Role.VA)
    users.set_owner(va_user.id, owner.id)
    job = _finished_run("va@example.com", tmp_path, "va.mp4")
    request = {"job_ids": [job], "login_ids": [login]}

    assert va.post("/clone/drive-upload", json=request).status_code == 403
    assert _files_in(drive, "ann.ig - Anna") == []
    bob = _client("bob@example.com")
    assert bob.post("/clone/drive-upload", json=request).status_code == 400

    r = ann.post("/clone/drive-upload", json=request)

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    assert len(_files_in(drive, "ann.ig - Anna")) == 1
    vas_page, owners_page = va.get("/clone").text, ann.get("/clone").text
    assert "In Drive: ann.ig - Anna" in vas_page
    assert state["drive_url"] not in vas_page
    assert 'id="card-drive-remove"' not in vas_page
    assert f'href="{state["drive_url"]}"' in owners_page
    [polled] = va.get("/clone/drive-uploads", params={"job": job}).json()
    assert (polled["id"], polled["status"]) == (state["id"], "done")
    assert bob.get("/clone/drive-uploads", params={"job": job}).json() == []


def _va_of(owner_email: str, email: str = "va@example.com") -> int:
    va = sign_in(TestClient(app), email, Role.VA)
    users.set_owner(va.id, users.by_email(owner_email).id)
    return va.id


def _cards(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'data-job-id="([^"]+)"', html)))


def test_the_owner_opens_drive_with_their_vas_video_to_upload(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    _va_of("ann@example.com")
    job = _finished_run("va@example.com", tmp_path, "va.mp4")

    page = ann.get("/drive", params={"upload": job}).text

    assert f'data-job="{job}"' in page
    assert 'id="dr-pick"' in page
    assert _upload(ann, job, login)["status"] == "done"


def test_made_by_shows_one_persons_clones(drive, tmp_path):
    ann = _client("ann@example.com")
    owner = users.by_email("ann@example.com").id
    va = _va_of("ann@example.com")
    mine = _finished_run("ann@example.com", tmp_path, "mine.mp4")
    theirs = _finished_run("va@example.com", tmp_path, "va.mp4")

    assert set(_cards(ann.get("/clone/gallery").text)) == {mine, theirs}
    assert _cards(ann.get("/clone/gallery", params={"va": va}).text) == [theirs]
    assert _cards(ann.get("/clone/gallery", params={"va": owner}).text) == [mine]
    page = ann.get("/clone", params={"va": va}).text
    assert _cards(page) == [theirs]
    button = page[page.index('class="menu menu-button made-by"') :]
    assert (
        "</svg> va@example.com</summary>" in button[: button.index("</summary>") + 10]
    )
    assert f'href="/clone?va={va}" aria-current="true"' in page
    assert f'<a class="menu-item menu-choice" href="/clone?va={owner}">' in page
    assert '<a class="menu-item menu-choice" href="/clone">' in page
    assert f'data-gallery-endpoint="/clone/gallery?va={va}"' in page
    assert f'data-live-endpoint="/clone/gallery/latest?va={va}"' in page
    assert _cards(ann.get("/clone/gallery/latest", params={"va": va}).text) == [theirs]


def test_made_by_takes_only_someone_whose_clones_the_reader_sees(drive, tmp_path):
    ann = _client("ann@example.com")
    bob = _client("bob@example.com")
    va = _va_of("ann@example.com")
    stranger = users.by_email("bob@example.com").id
    _finished_run("va@example.com", tmp_path, "va.mp4")

    assert ann.get("/clone", params={"va": stranger}).status_code == 404
    assert ann.get("/clone/gallery", params={"va": 999_999}).status_code == 404
    assert bob.get("/clone/gallery", params={"va": va}).status_code == 404
    assert ann.get("/clone/gallery", params={"va": "x"}).status_code == 422


def test_not_uploaded_leaves_only_finished_videos_in_no_drive_folder(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    va = _va_of("ann@example.com")
    posted = _finished_run("va@example.com", tmp_path, "posted.mp4")
    waiting = _finished_run("va@example.com", tmp_path, "waiting.mp4")
    taken_out = _finished_run("ann@example.com", tmp_path, "out.mp4")
    failed = create_job("clone", {}, actor=str(va))
    run_job(failed, _raises, {})
    left = {"not_uploaded": 1}

    assert set(_cards(ann.get("/clone/gallery", params=left).text)) == {
        posted,
        waiting,
        taken_out,
    }
    _upload(ann, posted, login)
    state = _upload(ann, taken_out, login)
    ann.post(f"/clone/drive-uploads/{state['id']}/remove")

    assert set(_cards(ann.get("/clone/gallery", params=left).text)) == {
        waiting,
        taken_out,
    }
    both = {"not_uploaded": 1, "va": va}
    assert _cards(ann.get("/clone/gallery", params=both).text) == [waiting]
    page = ann.get("/clone", params=both).text
    assert f'class="btn not-uploaded" href="/clone?va={va}" aria-pressed="true"' in page
    assert f"/clone/gallery?va={va}&amp;not_uploaded=1" in page
    off = ann.get("/clone", params={"va": va}).text
    assert (
        f'class="btn not-uploaded" href="/clone?va={va}&amp;not_uploaded=1"'
        ' aria-pressed="false"' in off
    )
    assert f'<input type="hidden" name="va" value="{va}">' in page
    assert '<input type="hidden" name="not_uploaded" value="1">' in page


def _raises():
    msg = "provider refused"
    raise RuntimeError(msg)


def test_a_va_and_a_person_without_vas_get_no_made_by(drive, tmp_path):
    ann = _client("ann@example.com")
    solo = _client("bob@example.com")
    _va_of("ann@example.com")
    va = TestClient(app)
    sign_in(va, "va@example.com", Role.VA)

    with_vas = ann.get("/clone").text
    assert "made-by" in with_vas
    assert "</svg> VAs</summary>" in with_vas
    solo_page = solo.get("/clone").text
    assert "made-by" not in solo_page
    assert "not-uploaded" in solo_page
    va_page = va.get("/clone").text
    assert "made-by" not in va_page
    assert "not-uploaded" not in va_page


@pytest.mark.parametrize(
    ("page", "fragment", "task"),
    [
        ("/generate", "/generate/gallery", "ai"),
        ("/download-assets", "/download-assets/gallery", "download_media"),
    ],
)
def test_the_vas_button_filters_gen_ai_and_download_too(page, fragment, task):
    ann = _client("ann@example.com")
    owner = users.by_email("ann@example.com").id
    va = _va_of("ann@example.com")
    mine = create_job(task, {}, actor=str(owner))
    theirs = create_job(task, {}, actor=str(va))

    html = ann.get(page).text

    assert "</svg> VAs</summary>" in html
    assert f'<a class="menu-item menu-choice" href="{page}?va={va}">' in html
    assert "not-uploaded" not in html
    assert set(_cards(ann.get(fragment).text)) == {mine, theirs}
    assert _cards(ann.get(fragment, params={"va": va}).text) == [theirs]
    assert f'data-gallery-endpoint="{fragment}?va={va}"' in (
        ann.get(page, params={"va": va}).text
    )


def _finished_spoof(email: str, tmp_path: Path) -> str:
    video = tmp_path / "IMG_4821.mov"
    video.write_bytes(b"\x00\x00\x00\x18ftypqt   fake")
    job_id = create_job(
        "repurpose",
        {"dir": str(tmp_path), "source": str(video), "seed": 1},
        actor=str(users.by_email(email).id),
    )
    run_job(job_id, lambda: [{"name": video.name, "path": str(video)}], {})
    return job_id


def test_a_spoofed_copy_goes_into_the_spoofed_folder_of_the_account(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_spoof("ann@example.com", tmp_path)

    r = ann.post("/clone/drive-upload", json={"job_ids": [job], "login_ids": [login]})

    assert r.status_code == 200, r.text
    [state] = ann.get("/clone/drive-uploads", params={"job": job}).json()
    assert state["status"] == "done"
    (account,) = drive.named("ann.ig - Anna", _root_of("ann@example.com"))
    (spoofed,) = drive.named("SPOOFED", account)
    assert len(drive.names_in(spoofed)) == 1
    assert _files_in(drive, "ann.ig - Anna") == ["SPOOFED"]


def _spoof_state(client: TestClient, job: str) -> str:
    [state] = client.get("/clone/drive-uploads", params={"job": job}).json()
    return state["status"]


def test_a_spoofed_copy_stays_in_drive_after_a_sync(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_spoof("ann@example.com", tmp_path)
    _upload(ann, job, login)

    with time_machine.travel(time.time() + 5):
        drive_folders.sync()

    assert _spoof_state(ann, job) == "done"


def test_an_upload_marked_gone_is_back_once_drive_shows_its_file(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_spoof("ann@example.com", tmp_path)
    upload = _upload(ann, job, login)
    upload_store.remove(upload["id"], "Removed in Drive")

    with time_machine.travel(time.time() + 5):
        drive_folders.sync()

    assert _spoof_state(ann, job) == "done"


def test_an_upload_its_owner_took_out_of_drive_stays_out(drive, tmp_path):
    ann = _client("ann@example.com")
    login = _account_folder(ann)
    job = _finished_spoof("ann@example.com", tmp_path)
    upload = _upload(ann, job, login)
    upload_store.remove(upload["id"])

    with time_machine.travel(time.time() + 5):
        drive_folders.sync()

    assert _spoof_state(ann, job) == "removed"
