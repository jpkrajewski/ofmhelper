"""/clone-links: pasted links checked (cleaned, downloaded, measured), priced,
run like /clone, and each result checked for glitches and uploaded into
the clicker's own Drive root: GOOD REELS when the model says GOOD,
TRIAL REELS for GLITCHY or a check that could not answer."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_KEY
from ofmhelpers.downloaders import media as downloader
from ofmhelpers.gdrive.fake import fake_drive
from ofmhelpers.llm.gemini.fake import FAKE_KEY as GEMINI_KEY
from ofmhelpers.llm.gemini.fake import fake_gemini
from ofmhelpers.utils import fake_media
from ofmhelpers.web import drive_folders
from ofmhelpers.web.clone_links import review
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.generation import clone_links
from ofmhelpers.web.routers.registry import RESUMABLE, TOOLS
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import ResultFile
from ofmhelpers.web.stores import clone_ai_checks, clone_from_links, users
from ofmhelpers.web.stores.jobs import get_job, list_jobs_page
from ofmhelpers.web.stores.users import _repository as user_repository

NEW = json.dumps([{"kind": "new"}])
REEL = "https://www.instagram.com/reel/ABC123/"
OTHER = "https://www.instagram.com/reel/XYZ789/"
PHONE = {
    "label": "Phone 01",
    "model_id": "iphone-13",
    "color_id": "blue",
    "owner": "Anna",
}
GLITCHY = {
    "reasoning": "At 0:03.0 the face melts into the wall.",
    "glitches": [{"at_s": 3.0, "what": "face melting", "severity": "blocking"}],
    "glitch_percent": 92,
    "verdict": "GLITCHY",
}


@pytest.fixture
def setup(fake_externals, monkeypatch, tmp_path):
    """Every outside service faked: downloads, Wan 3.0 (kie.ai), Gemini,
    Drive. Retries off: a failure is the answer."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path / "assets")
    monkeypatch.setattr(downloader, "_OUTPUT_ROOT", tmp_path / "downloads")
    return fake_drive()


def _client(email: str = "ann@example.com", *, google: bool = True) -> TestClient:
    c = TestClient(app)
    user = sign_in(c, email)
    if google:
        user_repository().signed_in(user.id, "", f"fake-refresh:{email}")
    return c


def _check(client: TestClient, text: str) -> dict:
    r = client.post("/clone-links/check", data={"links": text})
    assert r.status_code == 200, r.text
    return r.json()


def _ready_ids(checked: dict) -> list[str]:
    return [c["job_id"] for c in checked["checks"] if c["ready"]]


def _run(client: TestClient, check_ids: list[str], **data) -> dict:
    form = {
        "api_key": KIE_KEY,
        "provider": "kie",
        "image_manifest": NEW,
        "export": "original",
        "checks": check_ids,
        **data,
    }
    files = [("image", ("face.png", fake_media.image("salmon"), "image/png"))]
    r = client.post("/clone-links/run", data=form, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def _root(email: str = "ann@example.com") -> str:
    root = users.by_email(email).drive_root_id
    assert root, f"{email} has no Drive root"
    return root


def _files_in(drive, folder: str, email: str = "ann@example.com") -> list[str]:
    (folder_id,) = drive.named(folder, _root(email))
    return drive.names_in(folder_id)


def test_check_cleans_dedupes_and_downloads_every_link(setup):
    ann = _client()
    pasted = (
        f"{REEL}?igsh=abc123\n"
        "instagram.com/reels/ABC123\n"  # the same reel, another spelling
        f"\n{OTHER}"
    )

    found = _check(ann, pasted)

    assert found["links"] == [REEL, OTHER]
    assert found["repeated"] == 1
    rows = found["checks"]
    assert [r["url"] for r in rows] == [REEL, OTHER]
    for row in rows:
        assert row["status"] == "done"
        assert row["ready"] is True
        assert row["seconds"] == pytest.approx(2.0, abs=0.2)
        assert row["poster_url"] == f"/clone-links/files/{row['job_id']}/0/poster"
        assert ann.get(row["poster_url"]).status_code == 200


def test_a_link_without_a_video_fails_its_row_with_the_reason(setup):
    ann = _client()

    rows = _check(
        ann,
        "https://www.instagram.com/reel/fake-fail-1/\n"
        "https://www.instagram.com/p/PHOTO1/",
    )["checks"]

    removed, photos = rows
    assert removed["status"] == "failed"
    assert "Video unavailable" in removed["error"]
    assert photos["status"] == "failed"
    assert photos["error"] == "No video at this link (a photo post?)"
    assert not removed["ready"]
    assert not photos["ready"]


def test_a_line_of_two_links_refuses_the_paste_before_any_download(setup):
    """The VA's paste (2026-10-25): a reel copied twice onto one line. Never
    split or deduped behind their back: the whole paste is refused, naming
    the line."""
    ann = _client()
    glued = f"{OTHER}https://www.instagram.com/reel/XYZ789/?utm_source=ig_web"

    r = ann.post("/clone-links/check", data={"links": f"{REEL}\n{glued}"})

    assert r.status_code == 400
    assert r.json()["detail"] == {
        "message": "Please put one link per line",
        "lines": [{"number": 2, "text": glued, "reason": "2 links on one line"}],
    }
    owner = str(users.by_email("ann@example.com").id)
    assert list_jobs_page({clone_links.TASK_FETCH}, 0, 10, owner)[1] == 0


def test_junk_and_a_site_yt_dlp_does_not_support_refuse_the_paste(setup):
    """The VA's paste (2026-10-25): two lines of junk and a runpod docs page
    -- every one named with why, a real reel among them not downloaded."""
    ann = _client()
    docs = "https://abc-8077.proxy.runpod.net/docs#/default/twotensors_post"
    pasted = f"2. dsadasdasd\nasdasdasdasdas\n{docs}\n{REEL}"

    r = ann.post("/clone-links/check", data={"links": pasted})

    assert r.status_code == 400
    assert r.json()["detail"]["lines"] == [
        {"number": 1, "text": "2. dsadasdasd", "reason": "not a link"},
        {"number": 2, "text": "asdasdasdasdas", "reason": "not a link"},
        {
            "number": 3,
            "text": docs,
            "reason": "Please put Instagram, TikTok, Twitter etc.",
        },
    ]
    owner = str(users.by_email("ann@example.com").id)
    assert list_jobs_page({clone_links.TASK_FETCH}, 0, 10, owner)[1] == 0


def test_a_link_checked_again_is_not_downloaded_again(setup, monkeypatch):
    """Check, reload, Check the same reel: the first download is reused
    while its video is kept. A failed one, or one whose video is gone, is
    downloaded again."""
    ann = _client()
    [first] = _check(ann, REEL)["checks"]
    downloads = []
    real = clone_links.download
    monkeypatch.setattr(
        clone_links, "download", lambda url, c: downloads.append(url) or real(url, c)
    )

    [again] = _check(ann, f"{REEL}?igsh=reloaded")["checks"]

    assert again["job_id"] == first["job_id"]
    assert again["ready"] is True
    assert downloads == []
    Path(get_job(first["job_id"]).result[0]["path"]).unlink()
    [fresh] = _check(ann, REEL)["checks"]
    assert fresh["job_id"] != first["job_id"]
    assert downloads == [REEL]
    dead = "https://www.instagram.com/reel/fake-fail-3/"
    [failed] = _check(ann, dead)["checks"]
    [retried] = _check(ann, dead)["checks"]
    assert failed["status"] == retried["status"] == "failed"
    assert retried["job_id"] != failed["job_id"]


def test_a_video_over_15_s_is_flagged_and_left_out(setup, monkeypatch):
    ann = _client()
    monkeypatch.setattr(clone_links, "duration_s", lambda _path, _timeout: 20.4)

    [row] = _check(ann, REEL)["checks"]

    assert row["status"] == "done"
    assert row["too_long"] is True
    assert row["ready"] is False
    quote = ann.post(
        "/clone-links/quote",
        json={"provider": "kie", "resolution": "720P", "check_ids": [row["job_id"]]},
    ).json()
    assert quote["lines"] == []
    assert [s["job_id"] for s in quote["skipped"]] == [row["job_id"]]
    r = ann.post(
        "/clone-links/run",
        data={"api_key": KIE_KEY, "image_manifest": NEW, "checks": [row["job_id"]]},
        files=[("image", ("face.png", fake_media.image("salmon"), "image/png"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "No checked link is ready: Check links first"


def test_too_many_links_are_refused_before_any_download(setup, monkeypatch):
    ann = _client()
    monkeypatch.setenv("OFM_CLONE_LINKS_MAX", "1")

    r = ann.post("/clone-links/check", data={"links": f"{REEL}\n{OTHER}"})

    assert r.status_code == 400
    assert r.json()["detail"] == "At most 1 links at a time (2 pasted)"


def test_quote_is_length_times_the_providers_rate(setup):
    ann = _client()
    checked = _check(ann, f"{REEL}\n{OTHER}")
    ids = _ready_ids(checked)
    seconds = [c["seconds"] for c in checked["checks"]]
    price = clone_links.clone_pricing()["kie"]
    rate = price.credits_per_video_s["720P"]

    quote = ann.post(
        "/clone-links/quote",
        json={"provider": "kie", "resolution": "720P", "check_ids": ids},
    ).json()

    assert [line["job_id"] for line in quote["lines"]] == ids
    assert quote["credits"] == pytest.approx(sum(seconds) * rate, abs=0.1)
    assert quote["usd"] == pytest.approx(
        sum(seconds) * rate * price.usd_per_credit, abs=0.01
    )
    assert quote["lines"][0]["usd"] == pytest.approx(
        seconds[0] * rate * price.usd_per_credit, abs=0.01
    )


def test_generate_needs_google_drive_connected(setup):
    ann = _client(google=False)
    ids = _ready_ids(_check(ann, REEL))

    r = ann.post(
        "/clone-links/run",
        data={"api_key": KIE_KEY, "image_manifest": NEW, "checks": ids},
        files=[("image", ("face.png", fake_media.image("salmon"), "image/png"))],
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "Connect Google Drive on your Account page first"


def test_a_good_run_lands_in_good_reels_with_its_check_kept(setup):
    drive = setup
    ann = _client()
    ids = _ready_ids(_check(ann, REEL))

    queued = _run(ann, ids)

    [job_id] = queued["job_ids"]
    job = get_job(job_id)
    assert job.status == "done", job.error
    assert job.params["link"] == REEL
    assert "api_key" not in job.params
    [review] = clone_from_links.for_jobs([job_id])
    assert review.status == "done", review.error
    assert review.folder_name == "GOOD REELS"
    assert review.drive_url
    [uploaded] = _files_in(drive, "GOOD REELS")
    assert uploaded.startswith("VID_")
    # The root is the person's own "DEV - ALL ACCOUNTS".
    assert drive.item(_root()).name == drive_folders.root_name()
    check = review.check
    assert check is not None
    assert check.verdict == "GOOD"
    assert check.provider == "Gemini"
    assert check.prompt_system.startswith("You check short AI-generated videos")
    assert check.raw_answer
    assert check.stills_at[0] == 0.0
    assert check.job_id == job_id
    # The card says so.
    [state] = ann.get("/clone-links/reviews", params={"job": job_id}).json()
    assert state["settled"] is True
    assert "In Drive:" in state["html"]
    assert "GOOD REELS" in state["html"]
    assert REEL in state["html"]
    assert "GOOD 0%" in state["html"]


def test_a_glitchy_run_lands_in_trial_reels_with_the_glitches_shown(setup):
    drive = setup
    ann = _client()
    ids = _ready_ids(_check(ann, REEL))
    fake_gemini().answer_next(GLITCHY)

    [job_id] = _run(ann, ids)["job_ids"]

    [review] = clone_from_links.for_jobs([job_id])
    assert review.status == "done", review.error
    assert review.folder_name == "TRIAL REELS"
    assert len(_files_in(drive, "TRIAL REELS")) == 1
    assert review.check.glitch_percent == 92
    html = ann.get("/clone-links").text
    assert "GLITCHY 92%" in html
    assert "face melting" in html
    assert "the face melts into the wall" in html


def test_a_check_that_cannot_answer_sends_the_video_to_trial_reels(setup):
    drive = setup
    ann = _client()
    ids = _ready_ids(_check(ann, REEL))
    fake_gemini().fail_next(503, "UNAVAILABLE", "This model is overloaded")

    [job_id] = _run(ann, ids)["job_ids"]

    [review] = clone_from_links.for_jobs([job_id])
    assert review.status == "done"
    assert review.folder_name == "TRIAL REELS"
    assert len(_files_in(drive, "TRIAL REELS")) == 1
    assert review.check.verdict is None
    assert "Gemini is unavailable" in review.check.error
    [state] = ann.get("/clone-links/reviews", params={"job": job_id}).json()
    assert "Not checked" in state["html"]


def test_a_failed_upload_is_shown_and_check_again_finishes_it(setup, monkeypatch):
    drive = setup
    ann = _client()
    ids = _ready_ids(_check(ann, REEL))
    # Drive disconnected between Generate and the upload.
    monkeypatch.setattr(review, "refresh_token", lambda _user_id: "")

    [job_id] = _run(ann, ids)["job_ids"]

    [found] = clone_from_links.for_jobs([job_id])
    assert found.status == "failed"
    assert found.error == "Connect Google Drive on your Account page first"
    [state] = ann.get("/clone-links/reviews", params={"job": job_id}).json()
    assert state["settled"] is True
    assert "Check again" in state["html"]

    monkeypatch.setattr(review, "refresh_token", users.refresh_token)
    again = ann.post(f"/clone-links/reviews/{job_id}/retry")

    assert again.status_code == 200, again.text
    assert "In Drive:" in again.json()["html"]
    assert len(_files_in(drive, "GOOD REELS")) == 1
    assert len(clone_ai_checks.for_jobs([job_id])) == 2  # each check kept


def test_a_done_review_is_never_uploaded_twice(setup):
    drive = setup
    ann = _client()
    [job_id] = _run(ann, _ready_ids(_check(ann, REEL)))["job_ids"]

    ann.post(f"/clone-links/reviews/{job_id}/retry")
    clone_links.start_reviews(
        job_id, users.by_email("ann@example.com").id, get_job(job_id).local_paths()
    )

    assert len(_files_in(drive, "GOOD REELS")) == 1
    assert len(clone_ai_checks.for_jobs([job_id])) == 1


def test_nobody_sees_or_retries_another_persons_links_or_runs(setup):
    ann = _client()
    bob = _client("bob@example.com")
    checked = _check(ann, REEL)
    ids = _ready_ids(checked)
    [job_id] = _run(ann, ids)["job_ids"]

    assert bob.get("/clone-links/checks", params={"job": ids}).json() == []
    assert bob.get("/clone-links/reviews", params={"job": job_id}).json() == []
    assert bob.post(f"/clone-links/reviews/{job_id}/retry").status_code == 404
    quote = bob.post(
        "/clone-links/quote",
        json={"provider": "kie", "resolution": "720P", "check_ids": ids},
    ).json()
    assert quote["lines"] == []
    r = bob.post(
        "/clone-links/run",
        data={"api_key": KIE_KEY, "image_manifest": NEW, "checks": ids},
        files=[("image", ("face.png", fake_media.image("salmon"), "image/png"))],
    )
    assert r.status_code == 400
    assert job_id not in bob.get("/clone-links").text


def test_a_run_is_resumable_and_listed_like_every_tool(setup):
    assert RESUMABLE[clone_links.TASK] is clone_links.run_clone_link
    assert TOOLS[clone_links.TASK_FETCH].label == clone_links.FETCH_LABEL


def test_recovery_rerun_reviews_once(setup, monkeypatch, tmp_path):
    """A re-run (recovery) goes through the same entry: the video is
    reviewed once, whatever ran before."""
    ann = _client()
    video = tmp_path / "clone.mp4"
    video.write_bytes(fake_media.mp4())
    monkeypatch.setattr(
        clone_links,
        "_run_clone",
        lambda *_a, **_k: [ResultFile(name=video.name, path=str(video))],
    )
    [job_id] = _run(ann, _ready_ids(_check(ann, REEL)))["job_ids"]

    clone_links.run_clone_link(job_id, KIE_KEY, str(tmp_path), **get_job(job_id).params)

    [review] = clone_from_links.for_jobs([job_id])
    assert review.status == "done"
    assert len(clone_ai_checks.for_jobs([job_id])) == 1


def test_the_review_folders_are_not_listed_as_leftovers_on_drive(setup):
    drive = setup
    ann = _client()
    phone = ann.post("/phones/add", json=PHONE).json()["id"]
    ann.post(
        f"/phones/{phone}/logins", json={"service": "instagram", "username": "ann.ig"}
    )
    drive_folders.sync()
    _run(ann, _ready_ids(_check(ann, REEL)))
    fake_gemini().answer_next(GLITCHY)
    _run(ann, _ready_ids(_check(ann, OTHER)))
    drive.seed_folder("old stuff", _root(), owner="ann@example.com")
    ann_id = users.by_email("ann@example.com").id

    board = drive_folders.board(owner=ann_id, viewer=ann_id, live=True)

    assert {"GOOD REELS", "TRIAL REELS"} <= set(drive.names_in(_root()))
    assert [f.name for f in board.other_folders] == ["old stuff"]


def test_the_page_has_the_form_and_the_nav_entry(setup):
    ann = _client()

    html = ann.get("/clone-links").text

    assert 'href="/clone-links"' in html
    assert 'id="clone-links-form"' in html
    assert 'name="links"' in html
    assert "check-links-btn" in html
    assert 'id="clone-links-confirm"' in html
    assert "Google Drive not connected" not in html
    assert (
        "Google Drive not connected"
        in _client("bob@example.com", google=False).get("/clone-links").text
    )


def test_a_card_finished_while_open_has_its_review_polled(setup, tmp_path):
    ann = _client()
    [job_id] = _run(ann, _ready_ids(_check(ann, REEL)))["job_ids"]

    fragment = ann.get("/clone-links/gallery/latest").text

    assert f'data-job-id="{job_id}"' in fragment
    assert f'class="ai-check" data-job="{job_id}" data-settled' in fragment
    assert Path(get_job(job_id).local_paths()[0]).is_file()
