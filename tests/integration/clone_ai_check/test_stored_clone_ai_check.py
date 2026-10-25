"""A check kept in `clone_ai_checks` comes back whole: why it has no verdict
(kind + raw detail) and the tokens an answer used."""

from ofmhelpers.clone_ai_check import CloneAiCheck, Verdict, unanswered
from ofmhelpers.web.stores import clone_ai_checks


def test_a_failed_check_keeps_its_kind_and_raw_detail(tmp_path):
    check = unanswered(
        tmp_path / "reel.mp4",
        "No AI model answered",
        "quota",
        detail="gemini: LLMUnavailableError: 429 RESOURCE_EXHAUSTED",
    ).model_copy(update={"provider": "Gemini", "model": "gemini-3.8-flash"})

    saved = clone_ai_checks.save(check)
    found = clone_ai_checks.get(saved.id)

    assert found is not None
    assert found.verdict is None
    assert found.error == "No AI model answered"
    assert found.error_kind == "quota"
    assert found.error_detail == "gemini: LLMUnavailableError: 429 RESOURCE_EXHAUSTED"
    assert (found.provider, found.model) == ("Gemini", "gemini-3.8-flash")


def test_an_answered_check_keeps_the_tokens_it_used(tmp_path):
    check = CloneAiCheck(
        video=str(tmp_path / "reel.mp4"),
        frame_px=360,
        prompt_system="rules",
        prompt_text="text",
        verdict=Verdict.GOOD,
        glitch_percent=3,
        input_tokens=1200,
        thinking_tokens=300,
        output_tokens=80,
    )

    found = clone_ai_checks.get(clone_ai_checks.save(check).id)

    assert found is not None
    assert found.verdict is Verdict.GOOD
    assert found.error_kind is None
    assert found.error_detail is None
    assert (found.input_tokens, found.thinking_tokens, found.output_tokens) == (
        1200,
        300,
        80,
    )
