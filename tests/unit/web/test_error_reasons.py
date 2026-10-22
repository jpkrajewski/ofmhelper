"""A failed card shows one short reason, not the provider's raw message.
The messages below are the real ones jobs have failed with (prod data,
2026-10-22), ids shortened."""

import pytest

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
