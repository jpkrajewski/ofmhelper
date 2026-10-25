"""A failed card shows one short reason, not the provider's raw message.
The messages below are the real ones jobs have failed with (prod data,
2026-10-22), ids shortened."""

import pytest

from ofmhelpers.web.clone_links import run as clone_link_run
from ofmhelpers.web.schemas import JobStatusPayload
from ofmhelpers.web.schemas.errors import FALLBACK, error_reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            "createTask rejected: {'code': 402, 'msg': 'Credits insufficient : Your current balance isn't enough'}",
            "No credits",
        ),
        (
            "abc123 failed: The input or output was flagged as sensitive. Please try again with different inputs.",
            "Flagged content",
        ),
        (
            "kie.ai failed this generation: your content creation prompt contains NSFW, which isn't allowed",
            "Flagged content",
        ),
        (
            "Higgsfield failed this generation: rejected by content moderation",
            "Flagged content",
        ),
        (
            "createTask rejected: {'code': 422, 'msg': 'reference_image_urls resolution is out of range', 'data': None}",
            "Input out of range",
        ),
        (
            "createTask rejected: {'code': 422, 'msg': 'reference_video_urls duration must be between 1 and 15 seconds'}",
            "Input out of range",
        ),
        ("abc123 failed: Internal Error, Please try again later.", "Provider error"),
        (
            "500 Server Error: Internal Server Error for url: https://kieai.redpandaai.co/api/file-stream-upload",
            "Provider error",
        ),
        (
            "HTTPSConnectionPool(host='api.kie.ai', port=443): Read timed out. (read timeout=30)",
            "Timed out",
        ),
        ("kie.ai failed this generation: generate task timeout.", "Timed out"),
        (
            "createTask rejected: {'code': 500, 'msg': 'File type not supported', 'data': None}",
            "Bad input file",
        ),
        (
            "cannot identify image file 'uploads/assets/x__IMG_2745.PNG'",
            "Bad input file",
        ),
        (
            "Higgsfield failed this generation: The image dimensions or aspect ratio are not supported by this model.",
            "Bad input file",
        ),
        ("createTask rejected: {'code': 401, 'msg': 'Unauthorized'}", "Wrong API key"),
        (
            "createTask rejected: {'code': 429, 'msg': 'Too many requests'}",
            "Rate limited",
        ),
        ("Higgsfield failed this generation: Generation failed", FALLBACK),
        (None, FALLBACK),
        ("", FALLBACK),
    ],
)
def test_error_maps_to_one_short_reason(error, reason):
    assert error_reason(error) == reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ("Could not download this reel", "Could not download"),
        # yt-dlp's Instagram login wall and a site's 401 behind our own
        # words: never the AI provider's "Rate limited" / "Wrong API key".
        (
            (
                "Could not download this reel: The webpage request was redirected"
                " to the login page. You have exceeded the rate-limit for"
                " accessing posts anonymously"
            ),
            "Could not download",
        ),
        (
            (
                "Could not download this reel: video: HTTP Error 401: Unauthorized"
                " | images: '401 Unauthorized' for 'https://www.instagram.com/p/X/'"
            ),
            "Could not download",
        ),
        (
            (
                "Needs login: upload your cookies on the Cookies page"
                " (Instagram: rate-limit reached or login required)"
            ),
            "Needs login",
        ),
        ("Too long for Wan 3.0: 15.1 s (the most is 15 s)", "Too long"),
        ("Stopped: the Generate was stopped", "Stopped"),
    ],
)
def test_a_run_that_never_reached_the_provider_says_so(error, reason):
    """/clone-links' own fixed messages (web/clone_links/run.py) come first
    and only at the start of the message."""
    assert error_reason(error) == reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (clone_link_run.COULD_NOT_DOWNLOAD, "Could not download"),
        (clone_link_run.NO_VIDEO, "Could not download"),
        (clone_link_run.NOT_KEPT, "Could not download"),
        (clone_link_run.UNREADABLE, "Could not download"),
        (clone_link_run.NEEDS_LOGIN, "Needs login"),
        (clone_link_run.STOPPED, "Stopped"),
        (clone_link_run.REFETCH, "Still generating"),
        (clone_link_run.NOT_SAVED, "Still generating"),
    ],
)
def test_every_fixed_message_of_a_clone_links_run_has_its_label(error, reason):
    assert error_reason(error) == reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            "Higgsfield failed this generation: the video could not be made (401)",
            "Wrong API key",
        ),
        (
            "kie.ai failed this generation: generation stopped by moderation",
            "Flagged content",
        ),
        (
            "createTask rejected: {'code': 429, 'msg': 'too long a queue'}",
            "Rate limited",
        ),
    ],
)
def test_our_words_inside_a_providers_message_are_the_providers(error, reason):
    assert error_reason(error) == reason


def test_a_run_recovery_still_finishes_says_still_generating():
    """runner.wait's timeout: paid, remembered, finished by recovery -- not
    "Timed out" or "Failed", and a task id with 401 in it is no key
    problem."""
    error = (
        "kie.ai is still generating (task 8f2c-401-ab, waited 1800s). No action"
        " needed -- recovery finishes this job once kie.ai does."
    )

    assert error_reason(error) == "Still generating"


def test_status_payload_carries_the_reason_for_the_polling_cards():
    payload = JobStatusPayload(
        job_id="j",
        task="t",
        params={},
        status="failed",
        error="createTask rejected: {'code': 402}",
        result=[],
        failed_sources=[],
    )
    assert payload.model_dump()["error_reason"] == "No credits"
