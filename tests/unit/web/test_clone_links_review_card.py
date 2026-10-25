"""/clone-links review rows and cards as data: when a review counts as cut
off, what a done card says without a review, when the page stops polling,
and what the AI decision button and panel say."""

import pytest

from ofmhelpers.web.schemas import Job
from ofmhelpers.web.schemas.clone_ai_checks import (
    CHECK_FAILED,
    CloneFromLink,
    ReviewReason,
    StoredCloneAiCheck,
)
from ofmhelpers.web.schemas.gallery import CloneLinksCard

NOW = 1_800_000_000.0
STALE_S = 1800
HOURS_2 = 7200


def _review(status: str = "checking", **fields) -> CloneFromLink:
    return CloneFromLink(
        id=1,
        job_id="j1",
        file_index=0,
        user_id=7,
        status=status,
        created_at=NOW - HOURS_2,
        **fields,
    )


def _check(**fields) -> StoredCloneAiCheck:
    return StoredCloneAiCheck(
        id=1,
        video="/v.mp4",
        frame_px=360,
        prompt_system="",
        prompt_text="",
        created_at=NOW,
        **fields,
    )


def _card(*, path: str | None = "/v.mp4", review=None, status="done") -> CloneLinksCard:
    entry = {"name": "v.mp4", "path": path}
    if path is None:
        entry["remote_url"] = "https://kie.example/v.mp4"
    job = Job(
        id="j1",
        task="clone_links",
        status=status,
        result=[entry] if status == "done" else None,
        created_at=NOW,
    )
    return CloneLinksCard.from_job(
        job, [], [], task_label="", poll_prefix="/clone-links", review=review
    )


def test_a_review_waiting_in_the_queue_is_never_cut_off():
    """Queued two hours ago and not claimed yet: still waiting."""
    assert not _review(started_at=None).cut_off(NOW, STALE_S)


def test_a_review_claimed_long_ago_and_still_running_is_cut_off():
    assert _review(started_at=NOW - HOURS_2).cut_off(NOW, STALE_S)
    assert _review("uploading", started_at=NOW - HOURS_2).cut_off(NOW, STALE_S)


def test_a_review_claimed_recently_or_finished_is_not_cut_off():
    assert not _review(started_at=NOW - 60).cut_off(NOW, STALE_S)
    assert not _review("done", started_at=NOW - HOURS_2).cut_off(NOW, STALE_S)


def test_a_cut_off_review_keeps_its_card_polled():
    """Recovery queues it again (or fails it for good): the page must see
    that without a reload."""
    card = _card(review=_review(started_at=NOW - HOURS_2))

    assert card.review_settled is False


@pytest.mark.parametrize("status", ["done", "failed"])
def test_a_finished_review_settles_its_card(status):
    assert _card(review=_review(status)).review_settled is True


def test_a_done_run_without_a_review_says_the_check_did_not_start():
    card = _card()

    assert card.saved_here is True
    assert card.missing_review == ReviewReason.NOT_STARTED
    assert card.review_settled is True


def test_a_done_run_kept_only_by_the_provider_says_use_download():
    card = _card(path=None)

    assert card.saved_here is False
    assert card.missing_review == ReviewReason.NOT_SAVED


def test_a_card_with_a_review_or_not_done_has_no_missing_reason():
    assert _card(review=_review("done")).missing_review is None
    assert _card(status="running").missing_review is None


def test_the_decision_names_a_used_up_or_busy_ai_and_nothing_else():
    assert _check(verdict="GOOD").decision == "good"
    assert _check(verdict="GLITCHY").decision == "glitch"
    quota = _check(error="No AI model answered", error_kind="quota")
    assert quota.decision == "not done (AI busy or daily limit reached)"
    assert _check(error="Could not read the video", error_kind="failed").decision == (
        "not done"
    )


def test_the_panel_never_shows_a_raw_error():
    """A check that kept its kind has a safe short error; an older row's
    error may be the provider's raw text: a fixed line instead."""
    kept = _check(error="No AI model answered", error_kind="quota")
    raw = _check(error="429 RESOURCE_EXHAUSTED: You exceeded your quota")

    assert kept.shown_error == "No AI model answered"
    assert raw.shown_error == CHECK_FAILED
    assert _check(verdict="GOOD").shown_error is None
