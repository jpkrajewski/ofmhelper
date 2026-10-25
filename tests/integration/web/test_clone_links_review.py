"""/clone-links reviews (web/clone_links/review.py): a done run's video
checked, then uploaded into its owner's GOOD REELS or TRIAL REELS; claimed
by one worker at a time; resumed by recovery when cut off, when an upload
failed for a reason that may pass, or when a run never got its review --
never a second check of a saved one, never a second copy in Drive; a card
that names every failure in fixed words."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import time
from pathlib import Path

import pytest
from clone_links_helpers import (
    GLITCHY,
    GOOD,
    REEL,
    client,
    clone_links_env,  # noqa: F401 -- the fixture, used by `pytestmark`
    files_in,
)
from logfire.testing import CaptureLogfire
from rq.timeouts import JobTimeoutException
from sqlalchemy import update

from ofmhelpers.clone_ai_check import unanswered
from ofmhelpers.config import settings
from ofmhelpers.gdrive.fake import fake_drive
from ofmhelpers.llm.gemini.fake import fake_gemini
from ofmhelpers.utils import fake_media
from ofmhelpers.web import drive_folders
from ofmhelpers.web.clone_links import review
from ofmhelpers.web.db.models import CloneFromLinkRow
from ofmhelpers.web.db.session import session_scope
from ofmhelpers.web.download_names import download_name
from ofmhelpers.web.drive_space import DRIVE_FULL, drive_space
from ofmhelpers.web.schemas import ResultFile
from ofmhelpers.web.schemas.clone_ai_checks import CHECK_FAILED, ReviewReason
from ofmhelpers.web.stores import clone_ai_checks, users
from ofmhelpers.web.stores import clone_from_links as store
from ofmhelpers.web.stores.jobs import _repository as jobs_repository
from ofmhelpers.web.stores.jobs import create_job

pytestmark = pytest.mark.usefixtures("clone_links_env")

ANN = "ann@example.com"
BOB = "bob@example.com"
GOOD_REELS = "GOOD REELS"
TRIAL_REELS = "TRIAL REELS"


def _owner(email: str = ANN) -> int:
    found = users.by_email(email)
    assert found is not None
    return found.id


def _video(tmp_path: Path, name: str = "clone.mp4") -> Path:
    video = tmp_path / name
    video.write_bytes(fake_media.mp4())
    return video


def _done_run(video: Path | None, email: str = ANN, *, mirror: bool = False) -> str:
    """A finished /clone-links run of `email`'s whose one result is `video`
    (None: only the provider's copy)."""
    job_id = create_job(
        review.RUN_TASK, {"link": REEL, "mirror": mirror}, actor=str(_owner(email))
    )
    entry = (
        ResultFile(name=video.name, path=str(video))
        if video
        else ResultFile(name="clone.mp4", path=None, remote_url="https://kie.example/c")
    )
    jobs_repository().update_status(job_id, "done", result=[entry.to_result()])
    return job_id


def _reviewed(tmp_path: Path, email: str = ANN) -> tuple[str, Path]:
    """A done run of `email`'s, its review started and run (inline)."""
    video = _video(tmp_path)
    job_id = _done_run(video, email)
    review.start_reviews(job_id, _owner(email), [str(video)])
    return job_id, video


def _row(job_id: str):
    [found] = store.for_jobs([job_id])
    return found


def _set(review_id: int, **values) -> None:
    """A row as a killed worker or an old run left it."""
    with session_scope() as s:
        s.execute(
            update(CloneFromLinkRow)
            .where(CloneFromLinkRow.id == review_id)
            .values(**values)
        )


def _long_ago() -> float:
    return time.time() - settings.clone_links.review_stale_s - 60


def _card(c, job_id: str) -> dict:
    [state] = c.get("/clone-links/reviews", params={"job": job_id}).json()
    return state


# --- the review ----------------------------------------------------------------


def test_a_good_video_lands_in_good_reels_with_its_check_kept(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    fake_gemini().answer_next(GOOD)

    job_id, video = _reviewed(tmp_path)

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.folder_name == GOOD_REELS
    assert row.attempts == 1
    assert files_in(drive, GOOD_REELS) == [download_name(video.name)]
    assert drive.item(row.drive_file_id).name == download_name(video.name)
    assert row.check.verdict == "GOOD"
    assert row.check.job_id == job_id
    state = _card(ann, job_id)
    assert state["settled"] is True
    assert "In Drive:" in state["html"]
    assert "AI decision: good" in state["html"]


def test_a_glitchy_video_lands_in_trial_reels_with_its_glitches(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    fake_gemini().answer_next(GLITCHY)

    job_id, _ = _reviewed(tmp_path)

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.folder_name == TRIAL_REELS
    assert len(files_in(drive, TRIAL_REELS)) == 1
    html = _card(ann, job_id)["html"]
    assert "AI decision: glitch" in html
    assert "face melting" in html


def test_a_check_that_raises_still_sends_the_video_to_trial_reels(
    tmp_path, monkeypatch, capfire: CaptureLogfire
):
    """Not an LLM failure (a bug, an SDK error): no verdict, fixed words on
    the card, the raw text only in the log and the span."""
    drive = fake_drive()
    ann = client(ANN)

    def boom(_video, *, mirrored=False):
        msg = "boom at /app/uploads/secret.mp4"
        raise RuntimeError(msg)

    monkeypatch.setattr(review, "check_clone", boom)

    job_id, _ = _reviewed(tmp_path)

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.folder_name == TRIAL_REELS
    assert len(files_in(drive, TRIAL_REELS)) == 1
    assert row.check.verdict is None
    assert row.check.error == CHECK_FAILED
    html = _card(ann, job_id)["html"]
    assert "AI decision: not done" in html
    assert CHECK_FAILED in html
    assert "boom" not in html
    [span] = [
        s
        for s in capfire.exporter.exported_spans_as_dict()
        if s["name"] == "AI check {job_id}"
    ]
    assert span["attributes"]["logfire.level_num"] >= 17
    assert span["attributes"]["check_skipped"] == "failed"
    [event] = [e for e in span["events"] if e["name"] == "exception"]
    assert "boom" in event["attributes"]["exception.message"]


def test_a_used_up_ai_says_so_on_the_card_and_is_an_error_span(
    tmp_path, monkeypatch, capfire: CaptureLogfire
):
    ann = client(ANN)
    raw = "429 RESOURCE_EXHAUSTED: You exceeded your current quota"
    monkeypatch.setattr(
        review,
        "check_clone",
        lambda video, *, mirrored=False: unanswered(
            video, "No AI model answered", "quota", detail=raw
        ),
    )

    job_id, _ = _reviewed(tmp_path)

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.folder_name == TRIAL_REELS
    html = _card(ann, job_id)["html"]
    assert "AI decision: not done (AI busy or daily limit reached)" in html
    assert "RESOURCE_EXHAUSTED" not in html
    [span] = [
        s
        for s in capfire.exporter.exported_spans_as_dict()
        if s["name"] == "AI check {job_id}"
    ]
    assert span["attributes"]["logfire.level_num"] >= 17
    assert span["attributes"]["check_skipped"] == "quota"
    assert span["attributes"]["check_error_detail"] == raw


def test_a_mirrored_run_is_checked_as_mirrored(tmp_path, monkeypatch):
    client(ANN)
    seen = []

    def check(video, *, mirrored=False):
        seen.append(mirrored)
        return unanswered(video, CHECK_FAILED)

    monkeypatch.setattr(review, "check_clone", check)
    video = _video(tmp_path)
    job_id = _done_run(video, mirror=True)

    review.start_reviews(job_id, _owner(), [str(video)])

    assert seen == [True]


def test_rq_time_limit_ends_the_review_without_an_upload(tmp_path, monkeypatch):
    drive = fake_drive()
    ann = client(ANN)

    def out_of_time(_video, *, mirrored=False):
        msg = "Task exceeded maximum timeout value (1200 seconds)"
        raise JobTimeoutException(msg)

    monkeypatch.setattr(review, "check_clone", out_of_time)
    drive.clear_calls()

    job_id, _ = _reviewed(tmp_path)

    row = _row(job_id)
    assert row.status == "failed"
    assert row.error == ReviewReason.TIMED_OUT
    assert "upload" not in drive.calls
    html = _card(ann, job_id)["html"]
    assert "Not in Drive: Took too long" in html
    assert "maximum timeout" not in html


def test_a_gone_video_fails_before_any_check_or_drive_call(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    video.unlink()
    asked = len(fake_gemini().requests)
    drive.clear_calls()

    review.start_reviews(job_id, _owner(), [str(video)])

    row = _row(job_id)
    assert row.status == "failed"
    assert row.error == ReviewReason.VIDEO_GONE
    assert drive.calls == []
    assert len(fake_gemini().requests) == asked
    assert clone_ai_checks.for_jobs([job_id]) == []
    assert "Not in Drive: The video file is gone" in _card(ann, job_id)["html"]


def test_a_result_kept_only_by_the_provider_gets_a_failed_review_saying_so(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    job_id = _done_run(None)
    drive.clear_calls()

    started = review.start_reviews(job_id, _owner(), [None])

    row = _row(job_id)
    assert started == [row.id]
    assert row.status == "failed"
    assert row.error == ReviewReason.NOT_SAVED
    assert "upload" not in drive.calls
    state = _card(ann, job_id)
    assert state["settled"] is True
    assert "Not in Drive: The video was not saved here: use Download" in state["html"]


def test_a_queue_that_refuses_the_review_fails_it_with_fixed_words(
    tmp_path, monkeypatch
):
    ann = client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)

    def refused(*_args, **_kwargs):
        msg = "Error 111 connecting to redis:6379. Connection refused."
        raise ConnectionError(msg)

    monkeypatch.setattr(review, "enqueue", refused)

    review.start_reviews(job_id, _owner(), [str(video)])

    row = _row(job_id)
    assert row.status == "failed"
    assert row.error == ReviewReason.QUEUE_FAILED
    html = _card(ann, job_id)["html"]
    assert "Not in Drive: Could not start the AI check" in html
    assert "redis" not in html


def test_start_reviews_never_raises_even_when_nothing_can_be_saved(
    tmp_path, monkeypatch
):
    client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)

    def broken(*_args, **_kwargs):
        msg = "database is down"
        raise RuntimeError(msg)

    monkeypatch.setattr(review, "enqueue", broken)
    monkeypatch.setattr(store, "finish", broken)

    assert review.start_reviews(job_id, _owner(), [str(video)]) == []


# --- one worker at a time --------------------------------------------------------


def test_a_review_is_claimed_once(tmp_path):
    """A second queued job for the same review does nothing."""
    client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))

    first = store.claim(review_id)

    assert first is not None
    assert first.attempts == 1
    assert first.started_at is not None
    assert store.claim(review_id) is None
    asked = len(fake_gemini().requests)
    review.review_file(review_id)
    assert len(fake_gemini().requests) == asked
    assert clone_ai_checks.for_jobs([job_id]) == []
    assert _row(job_id).status == "checking"


def test_a_run_whose_row_was_taken_over_writes_and_uploads_nothing(
    tmp_path, monkeypatch
):
    """Worker A stalls in its check; recovery hands the row to worker B,
    which finishes it. A's check is kept, but A neither moves the row nor
    uploads a second copy."""
    drive = fake_drive()
    client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))
    real = review.check_clone
    calls = []

    def stalling(path, *, mirrored=False):
        calls.append(path)
        if len(calls) == 1:
            assert store.release(_row(job_id))
            review.review_file(review_id)
        return real(path, mirrored=mirrored)

    monkeypatch.setattr(review, "check_clone", stalling)

    review.review_file(review_id)

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.attempts == 2
    assert len(files_in(drive, GOOD_REELS)) == 1
    assert len(clone_ai_checks.for_jobs([job_id])) == 2


def test_an_old_claim_cannot_check_or_finish_the_row():
    client(ANN)
    job_id = _done_run(None)
    review_id = store.start(job_id, 0, _owner(), "/v.mp4")
    old = store.claim(review_id)
    assert store.release(old)
    new = store.claim(review_id)

    assert store.checked(old, None, GOOD_REELS) is False
    assert store.finish(review_id, claimed=old.started_at, error="x") is False
    assert store.checked(new, None, GOOD_REELS) is True
    assert store.finish(review_id, claimed=new.started_at, drive_file_id="f") is True
    assert _row(job_id).status == "done"


def test_a_queued_review_is_restarted_only_once_really_stale():
    client(ANN)
    job_id = _done_run(None)
    review_id = store.start(job_id, 0, _owner(), "/v.mp4")

    assert store.start(job_id, 0, _owner(), "/v.mp4") is None
    _set(review_id, created_at=_long_ago())
    assert store.start(job_id, 0, _owner(), "/v.mp4") == review_id
    row = _row(job_id)
    assert row.started_at is None
    assert row.attempts == 0


# --- whose ---------------------------------------------------------------------


def test_another_users_row_is_never_shown_or_restarted(tmp_path):
    client(ANN)
    client(BOB)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))
    store.finish(review_id, claimed=None, error=ReviewReason.QUEUE_FAILED)

    assert store.start(job_id, 0, _owner(BOB), str(video)) is None
    assert store.for_jobs([job_id], user_id=_owner(BOB)) == []
    [row] = store.for_jobs([job_id], user_id=_owner())
    assert row.user_id == _owner()
    assert row.status == "failed"


# --- recovery ------------------------------------------------------------------


def test_a_cut_off_review_is_queued_again_once(tmp_path):
    """Its worker was killed mid-check (a deploy): recovery queues it once,
    it finishes, and the next sweep leaves it alone."""
    drive = fake_drive()
    ann = client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))
    store.claim(review_id)
    _set(review_id, started_at=_long_ago())
    assert _card(ann, job_id)["settled"] is False

    outcomes = review.resume_reviews()

    assert outcomes[f"review {review_id}"] == "requeued"
    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.attempts == 2
    assert len(files_in(drive, GOOD_REELS)) == 1
    assert f"review {review_id}" not in review.resume_reviews()
    assert len(files_in(drive, GOOD_REELS)) == 1


def test_a_review_cut_off_too_often_is_given_up(tmp_path):
    ann = client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))
    store.claim(review_id)
    _set(
        review_id,
        started_at=_long_ago(),
        attempts=settings.clone_links.review_attempts,
    )

    assert review.resume_reviews()[f"review {review_id}"] == "gave up"

    row = _row(job_id)
    assert row.status == "failed"
    assert row.error == ReviewReason.CUT_OFF
    state = _card(ann, job_id)
    assert state["settled"] is True
    assert "Not in Drive: Cut off by a restart" in state["html"]


def test_a_failed_upload_is_tried_again_without_a_second_check(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    video = _video(tmp_path)
    drive.fail_on(download_name(video.name))
    job_id = _done_run(video)
    review.start_reviews(job_id, _owner(), [str(video)])
    failed = _row(job_id)
    assert failed.status == "failed"
    assert failed.folder_name == GOOD_REELS
    assert "fake outage" not in _card(ann, job_id)["html"]
    drive.fail_on()
    asked = len(fake_gemini().requests)

    outcomes = review.resume_reviews()

    assert outcomes[f"review {failed.id}"] == "retried"
    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.check_id == failed.check_id
    assert len(fake_gemini().requests) == asked
    assert len(clone_ai_checks.for_jobs([job_id])) == 1
    assert len(files_in(drive, GOOD_REELS)) == 1


def test_a_failed_upload_is_tried_only_while_attempts_are_left(tmp_path):
    drive = fake_drive()
    client(ANN)
    video = _video(tmp_path)
    drive.fail_on(download_name(video.name))
    job_id = _done_run(video)
    review.start_reviews(job_id, _owner(), [str(video)])

    for _ in range(settings.clone_links.review_attempts + 2):
        review.resume_reviews()

    row = _row(job_id)
    assert row.status == "failed"
    assert row.attempts == settings.clone_links.review_attempts
    assert f"review {row.id}" not in review.resume_reviews()


def test_a_full_drive_is_tried_again_only_once_it_has_room(tmp_path):
    drive = fake_drive()
    client(ANN)
    drive.set_storage(limit=1)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review.start_reviews(job_id, _owner(), [str(video)])
    assert _row(job_id).error == DRIVE_FULL

    assert review.resume_reviews()[f"review {_row(job_id).id}"] == "waiting for Drive"
    assert _row(job_id).attempts == 1

    drive.set_storage(limit=100 * 10**9)
    drive_space(_owner())  # a page asks Google: room again

    assert review.resume_reviews()[f"review {_row(job_id).id}"] == "retried"
    assert _row(job_id).status == "done"


def test_an_upload_cut_off_after_it_reached_drive_is_adopted_not_doubled(tmp_path):
    """Killed between Drive's answer and the row's update: the next run
    finds the file in the folder and keeps it."""
    drive = fake_drive()
    client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    review_id = store.start(job_id, 0, _owner(), str(video))
    claimed = store.claim(review_id)
    assert store.checked(claimed, None, GOOD_REELS)
    _, folder_id = drive_folders.review_folder(
        users.refresh_token(_owner()), _owner(), GOOD_REELS
    )
    made = drive.add_file(folder_id, download_name(video.name), "video/mp4")
    _set(review_id, started_at=_long_ago())
    drive.clear_calls()
    asked = len(fake_gemini().requests)

    review.resume_reviews()

    row = _row(job_id)
    assert row.status == "done", row.error
    assert row.drive_file_id == made
    assert "upload" not in drive.calls
    assert len(fake_gemini().requests) == asked
    assert files_in(drive, GOOD_REELS) == [download_name(video.name)]


def test_a_queue_failure_is_queued_again(tmp_path, monkeypatch):
    drive = fake_drive()
    client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    real = review.enqueue

    def refused(*_args, **_kwargs):
        msg = "Connection refused"
        raise ConnectionError(msg)

    monkeypatch.setattr(review, "enqueue", refused)
    review.start_reviews(job_id, _owner(), [str(video)])
    monkeypatch.setattr(review, "enqueue", real)

    assert review.resume_reviews()[f"review {_row(job_id).id}"] == "retried"

    assert _row(job_id).status == "done"
    assert len(files_in(drive, GOOD_REELS)) == 1


def test_a_done_run_without_a_review_gets_one(tmp_path):
    """Its review never started (a DB hiccup at the end of the run): the
    card says so, then recovery starts it."""
    drive = fake_drive()
    ann = client(ANN)
    video = _video(tmp_path)
    job_id = _done_run(video)
    state = _card(ann, job_id)
    assert "Not in Drive: The AI check did not start" in state["html"]

    outcomes = review.resume_reviews()

    assert outcomes[f"job {job_id}"] == "review started"
    assert _row(job_id).status == "done"
    assert len(files_in(drive, GOOD_REELS)) == 1
    assert f"job {job_id}" not in review.resume_reviews()


def test_a_done_run_kept_only_by_the_provider_is_marked_once(tmp_path):
    drive = fake_drive()
    ann = client(ANN)
    job_id = _done_run(None)
    assert "The video was not saved here: use Download" in _card(ann, job_id)["html"]
    drive.clear_calls()

    review.resume_reviews()

    row = _row(job_id)
    assert row.status == "failed"
    assert row.error == ReviewReason.NOT_SAVED
    assert "upload" not in drive.calls
    assert review.resume_reviews() == {}


def test_recovery_never_starts_another_users_review_on_a_run(tmp_path):
    """A job's review row belongs to the job's owner, whoever recovery
    sweeps for."""
    client(ANN)
    client(BOB)
    video = _video(tmp_path)
    job_id = _done_run(video, BOB)

    review.resume_reviews()

    row = _row(job_id)
    assert row.user_id == _owner(BOB)
    assert store.for_jobs([job_id], user_id=_owner()) == []
