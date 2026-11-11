"""What a failed card shows (`schemas.errors.error_line`): who refused, the
reason and what they said, on the card itself."""

import pytest

from ofmhelpers.schemas.errors import error_line, error_reason


@pytest.mark.parametrize(
    ("error", "line"),
    [
        (
            (
                "APIYI: flagged by moderation: [DataInspectionFailed] Green net check"
                " rejected image (input)"
            ),
            (
                "APIYI: Flagged content, flagged by moderation: [DataInspectionFailed]"
                " Green net check rejected image (input)"
            ),
        ),
        (
            (
                "APIYI: insufficient balance. APIYI: user [97415] quota [5927]"
                " preConsumedQuota [20000] is not enough (insufficient_user_quota,"
                " HTTP 403)"
            ),
            (
                "APIYI: No credits, insufficient balance. APIYI: user [97415] quota"
                " [5927] preConsumedQuota [20000] is not enough"
                " (insufficient_user_quota, HTTP 403)"
            ),
        ),
        ("kie.ai: wrong API key", "kie.ai: Wrong API key"),
        ("Higgsfield: wrong API key", "Higgsfield: Wrong API key"),
        # Ours: the reason is already its first words.
        (
            "Too long: 42 s, the model takes 15 s",
            "Too long: 42 s, the model takes 15 s",
        ),
        ("Could not download this reel", "Could not download this reel"),
        ("Stopped", "Stopped"),
        (
            "kie.ai is still generating (task t-401)",
            "kie.ai is still generating (task t-401)",
        ),
        # Already says it failed: nothing put in front; line breaks flattened.
        ("the export  failed\nbadly", "the export failed badly"),
        ("No space left on device", "Failed, No space left on device"),
        (None, "Failed"),
        ("", "Failed"),
    ],
)
def test_a_failure_reads_who_why_and_what_they_said(error, line):
    assert error_line(error) == line


def test_a_long_answer_is_cut_and_the_reason_stays():
    error = "APIYI: flagged by moderation: " + "x" * 500

    line = error_line(error)

    assert line.startswith("APIYI: Flagged content, flagged by moderation: xxx")
    assert len(line) == 240
    assert line.endswith("…")
    assert error_reason(error) == "Flagged content"
