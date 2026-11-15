"""A failed card shows one short reason, not the provider's raw message.
The messages below are the real ones jobs have failed with (prod data,
2026-10-22), ids shortened."""

import pytest

from ofmhelpers.features.clone_links import run as clone_link_run
from ofmhelpers.schemas import JobStatusPayload
from ofmhelpers.schemas.errors import FALLBACK, download_reason, error_reason


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
        # A downloader's words for a link asked for again (prod 2026-11-12,
        # job 65a6bb46, a reel of a banned profile).
        (
            "ERROR: [Instagram] DdolTu_JpJ0: Instagram sent an empty media response. Check if this post is accessible in your browser without being logged-in.",
            "Post deleted or profile banned",
        ),
        (
            "ERROR: [Instagram] DdolTu_JpJ0: The webpage request was redirected to the login page. You have exceeded the rate-limit for accessing posts anonymously. Use --cookies-from-browser or --cookies for the authentication.",
            "Needs login",
        ),
        ("ERROR: [youtube] abc: Video unavailable", "Post deleted or profile banned"),
        (
            "Higgsfield failed this generation: Your credit balance is too low to complete this request. Please top up your balance and try again.",
            "No credits",
        ),
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


# What the downloaders really said, from the prod snapshot of 2026-11-15
# (ids shortened): each failed link says why on its card.
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            (
                "video: ERROR: [Instagram] DesPzbYsp: This content is only available for"
                " registered users who follow this account. Use --cookies-from-browser"
                " | images: [instagram][error] HttpError: '400 Bad Request'"
            ),
            "Private account: only its followers can see it",
        ),
        (
            (
                "images: [instagram][error] HttpError: '401 Unauthorized' for 'https://"
                "www.instagram.com/api/v1/media/1/info/' | video: ERROR: [Instagram] Dc1:"
                " There is no video in this post"
            ),
            "No video in this post (photos only)",
        ),
        (
            "ERROR: [Instagram] Dc1: No video formats found!; please report this issue",
            "Instagram gave no video (private, age-restricted or blocked)",
        ),
        (
            "video: ERROR: [Instagram] Dc1: Video unavailable",
            "Post deleted or profile banned",
        ),
        (
            "video: ERROR: Unsupported URL: https://xhamster19.com/gay/creators/x/shorts",
            "Not a link we can download",
        ),
        (
            (
                "video: x: The server may not support the current cipher list. | images:"
                " This post needs a login: upload your fresh cookies.txt at /cookies"
            ),
            "Instagram wants a login: upload fresh cookies on Cookies",
        ),
        (
            "video: ERROR: [youtube] abc: Sign in to confirm you're not a bot.",
            "YouTube wants a login (bot check): upload YouTube cookies on Cookies",
        ),
        (
            (
                "video: ERROR: [PornHub] 1: Unable to download webpage: HTTP Error 403:"
                " Forbidden"
            ),
            "The site refused us (403)",
        ),
        (
            (
                "ERROR: [Instagram] Dc1: Video info extraction failed: HTTP Error 400: Bad"
                " Request (caused by <HTTPError 400: Bad Request>)"
            ),
            "Instagram blocked the download for now: try again later",
        ),
        (
            (
                "video: ERROR: [Instagram] Dc1: Unable to download webpage: Failed to"
                " perform, curl: (7) CONNECT tunnel failed, response 502."
            ),
            "Connection failed (proxy or site down): try again",
        ),
        (
            (
                "video: ERROR: [Instagram] Dc1: Something nobody knew yet; please report"
                " this issue"
            ),
            "Something nobody knew yet",
        ),
    ],
)
def test_a_failed_link_says_the_real_reason(error, reason):
    assert download_reason(error) == reason
