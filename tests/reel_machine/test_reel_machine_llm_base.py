"""
strip_llm_preamble: an LLM rewrite of the prompt package sometimes prepends
commentary ("Here is the rewritten prompt:") or wraps the text in a ```
code fence before the actual SETUP...COST package -- that must never reach
the user-facing textarea. If the SETUP marker is missing entirely, the
response isn't a valid rewrite and the original draft is returned instead.
"""

from ofmhelpers.reel_machine.llm.base import strip_llm_preamble

DRAFT = "SETUP\n  something\n\nCOST / RISK\n  ~$3"


def test_returns_clean_text_unchanged():
    assert strip_llm_preamble(DRAFT, DRAFT) == DRAFT


def test_strips_leading_commentary_before_setup():
    leaked = f"Here is the rewritten prompt package:\n\n{DRAFT}"
    assert strip_llm_preamble(leaked, DRAFT) == DRAFT


def test_strips_wrapping_code_fence():
    fenced = f"```\n{DRAFT}\n```"
    assert strip_llm_preamble(fenced, DRAFT) == DRAFT


def test_strips_code_fence_with_language_tag():
    fenced = f"```text\n{DRAFT}\n```"
    assert strip_llm_preamble(fenced, DRAFT) == DRAFT


def test_falls_back_to_draft_when_setup_marker_is_missing():
    assert strip_llm_preamble("Sorry, I can't help with that.", DRAFT) == DRAFT
