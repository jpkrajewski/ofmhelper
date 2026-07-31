"""
The text provider used by the second pass, and how the registry hands it out.

Two things are load-bearing: the request is OpenAI-shaped with JSON mode on
(a chatty model otherwise wraps the answer in prose), and a missing key makes
`get_text_provider` answer None rather than raise -- pass 2 runs after the
download and the analysis, so nobody may lose a job over it.
"""

from unittest import mock

import pytest

from ofmhelpers.reel_machine.llm import groq_provider, registry
from ofmhelpers.reel_machine.llm.groq_provider import GroqProvider


def _answer(content: str):
    response = mock.Mock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def test_asks_for_json_and_returns_the_raw_answer():
    with mock.patch.object(
        groq_provider.requests, "post", return_value=_answer('{"ok": true}')
    ) as post:
        answer = GroqProvider(api_key="k", model="m").complete_json("what is this")

    assert answer == '{"ok": true}'
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "m"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"] == [{"role": "user", "content": "what is this"}]
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer k"
    # Never open-ended: this runs inside a worker slot.
    assert post.call_args.kwargs["timeout"] > 0


def test_an_http_error_is_raised_for_the_caller_to_swallow():
    """The provider doesn't decide that a failure is survivable -- hunt.py
    does. This just has to fail loudly enough to be caught."""
    response = mock.Mock()
    response.raise_for_status.side_effect = OSError("503")
    with (
        mock.patch.object(groq_provider.requests, "post", return_value=response),
        pytest.raises(OSError, match="503"),
    ):
        GroqProvider(api_key="k").complete_json("go")


def test_no_key_means_no_text_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert registry.get_text_provider() is None


def test_a_configured_key_gives_the_groq_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    provider = registry.get_text_provider()

    assert isinstance(provider, GroqProvider)
    assert provider.name == "groq"


def test_an_unknown_text_provider_name_still_raises(monkeypatch):
    """A typo in the deployment is not something to silently skip."""
    monkeypatch.setenv("GROQ_API_KEY", "k")

    with pytest.raises(ValueError, match="unknown text provider"):
        registry.get_text_provider("gpt5")
