"""
The registry deliberately has no fallback provider any more: an unusable
choice must raise so the job fails with a readable reason, instead of
silently swapping in a model nobody picked.

`settings.reel_machine` is constructed fresh on every access (see
config/__init__.py), so these drive it with monkeypatch.setenv rather than
by patching an instance.
"""

from unittest import mock

import pytest

from ofmhelpers.reel_machine.llm import registry


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("REEL_MACHINE_LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert registry.get_provider().name == "gemini"


def test_explicit_name_wins_over_the_env_var(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with mock.patch(
        "ofmhelpers.reel_machine.llm.anthropic_provider.AnthropicProvider"
    ) as provider:
        provider.return_value.name = "anthropic"
        assert registry.get_provider("anthropic").name == "anthropic"


def test_env_var_selects_the_provider(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert registry.get_provider().name == "anthropic"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with pytest.raises(ValueError, match="unknown provider"):
        registry.get_provider("groq")


def test_missing_api_key_raises_instead_of_falling_back():
    with pytest.raises(KeyError, match="GEMINI_API_KEY"):
        registry.get_provider("gemini")
