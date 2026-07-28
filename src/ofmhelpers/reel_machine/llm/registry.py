"""
Picks the active LLMProvider by name or from the REEL_MACHINE_LLM_PROVIDER
env var, defaulting to Gemini (the only free one that takes video).

Unlike the old registry there is no silent fallback provider: a missing
package or API key raises, which fails the job with "GEMINI_API_KEY" rather
than quietly producing a worse prompt from a stand-in nobody chose.
"""

from ofmhelpers.config import settings
from ofmhelpers.reel_machine.llm.base import LLMProvider

PROVIDER_NAMES = ("gemini", "anthropic")
DEFAULT_PROVIDER = "gemini"


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.reel_machine.llm_provider or DEFAULT_PROVIDER
    if name not in PROVIDER_NAMES:
        msg = f"unknown provider {name!r} (expected one of {', '.join(PROVIDER_NAMES)})"
        raise ValueError(msg)

    if name == "anthropic":
        from ofmhelpers.reel_machine.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider

    return GeminiProvider()
