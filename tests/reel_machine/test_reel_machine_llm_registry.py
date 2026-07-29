"""
The registry has no fallback provider: an unusable choice must raise so the
job fails with a readable reason, instead of silently swapping in a model
nobody picked.

`settings.reel_machine` is constructed fresh on every access (see
config/__init__.py), so these drive it with monkeypatch.setenv rather than
by patching an instance.
"""

import pytest

from ofmhelpers.reel_machine.llm import registry


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("REEL_MACHINE_LLM_PROVIDER", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert registry.get_provider().name == "gemini"


def test_explicit_name_wins_over_the_env_var(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "nope")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert registry.get_provider("gemini").name == "gemini"


def test_env_var_selects_the_provider(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert registry.get_provider().name == "gemini"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with pytest.raises(ValueError, match="unknown provider 'groq'"):
        registry.get_provider("groq")


def test_missing_api_key_raises_instead_of_falling_back():
    with pytest.raises(KeyError, match="GEMINI_API_KEY"):
        registry.get_provider("gemini")


def test_every_registered_name_matches_its_provider():
    """The map is keyed off each class's own `name`, so a provider renamed in
    one place can't become unreachable under the other."""
    for name, factory in registry.PROVIDERS.items():
        assert factory.name == name
