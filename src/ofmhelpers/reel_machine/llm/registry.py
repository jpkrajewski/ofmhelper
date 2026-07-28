"""
Picks the active LLMProvider by name or from the REEL_MACHINE_LLM_PROVIDER
env var, defaulting to Gemini (the only free API that takes video).

No fallback provider: an unknown name or a missing API key raises, which
fails the job with "GEMINI_API_KEY" rather than quietly producing a worse
prompt from a stand-in nobody chose.
"""

from collections.abc import Callable

from ofmhelpers.config import settings
from ofmhelpers.reel_machine.llm import LLMProvider
from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider

PROVIDERS: dict[str, Callable[[], LLMProvider]] = {
    GeminiProvider.name: GeminiProvider,
}
DEFAULT_PROVIDER = GeminiProvider.name


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.reel_machine.llm_provider or DEFAULT_PROVIDER
    try:
        factory = PROVIDERS[name]
    except KeyError:
        msg = f"unknown provider {name!r} (expected one of {', '.join(PROVIDERS)})"
        raise ValueError(msg) from None
    return factory()
