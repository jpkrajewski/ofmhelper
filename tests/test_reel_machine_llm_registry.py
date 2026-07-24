"""
get_provider() must default to the free TemplateProvider and fall back to it
whenever a paid/opt-in provider can't be constructed (missing package,
missing API key) -- a misconfigured LLM choice should degrade the script
quality, never fail the whole /replicate job.
"""

from ofmhelpers.reel_machine.llm.registry import get_provider
from ofmhelpers.reel_machine.llm.template_provider import TemplateProvider


def test_defaults_to_template_with_no_env_var(monkeypatch):
    monkeypatch.delenv("REEL_MACHINE_LLM_PROVIDER", raising=False)
    assert isinstance(get_provider(), TemplateProvider)


def test_unknown_name_falls_back_to_template():
    assert isinstance(get_provider("not-a-real-provider"), TemplateProvider)


def test_groq_without_api_key_falls_back_to_template(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = get_provider("groq")
    assert isinstance(provider, TemplateProvider)


def test_anthropic_without_api_key_falls_back_to_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = get_provider("anthropic")
    assert isinstance(provider, TemplateProvider)


def test_gemini_without_api_key_falls_back_to_template(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = get_provider("gemini")
    assert isinstance(provider, TemplateProvider)


def test_groq_analyze_reel_is_a_safe_noop_without_a_vision_model(monkeypatch, tmp_path):
    """Groq's current model catalog has no vision-capable model -- analyze_reel
    must not attempt a doomed API call by default, just return {} like the
    template provider does."""
    monkeypatch.delenv("GROQ_VISION_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from ofmhelpers.reel_machine.llm.groq_provider import GroqProvider

    provider = GroqProvider()
    assert provider.analyze_reel(tmp_path / "x.jpg", "transcript") == {}


def test_env_var_is_used_when_name_omitted(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "not-real")
    assert isinstance(get_provider(), TemplateProvider)


def test_explicit_name_overrides_env_var(monkeypatch):
    monkeypatch.setenv("REEL_MACHINE_LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # explicit "template" wins over the env var, and needs no provider construction
    assert isinstance(get_provider("template"), TemplateProvider)
