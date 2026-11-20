import pytest

from ofmhelpers.features.jobs.schema import (
    ErrorText,
    error_line,
    error_reason,
    error_text,
)


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


@pytest.mark.parametrize(
    ("error", "head", "said", "about_keys"),
    [
        (
            (
                "APIYI: flagged by moderation: [DataInspectionFailed] Green net check"
                " rejected image (input)"
            ),
            "APIYI: Flagged content",
            (
                "flagged by moderation: [DataInspectionFailed] Green net check"
                " rejected image (input)"
            ),
            False,
        ),
        ("kie.ai: wrong API key", "kie.ai: Wrong API key", "", True),
        (
            "APIYI: insufficient balance (insufficient_user_quota, HTTP 403)",
            "APIYI: No credits",
            "insufficient balance (insufficient_user_quota, HTTP 403)",
            True,
        ),
        (
            "Too long: 42 s, the model takes 15 s",
            "Too long",
            "42 s, the model takes 15 s",
            False,
        ),
        ("Could not download this reel", "Could not download", "this reel", False),
        ("No space left on device", "Failed", "No space left on device", False),
        (None, "Failed", "", False),
        ("", "Failed", "", False),
    ],
)
def test_a_failed_card_puts_who_and_why_first_and_what_was_said_under_it(
    error, head, said, about_keys
):
    assert error_text(error) == ErrorText(head=head, said=said, about_keys=about_keys)


def test_what_was_said_is_cut_like_the_line():
    text = error_text("APIYI: flagged by moderation: " + "x" * 500)

    assert text.head == "APIYI: Flagged content"
    assert len(text.said) == 240
    assert text.said.endswith("…")
