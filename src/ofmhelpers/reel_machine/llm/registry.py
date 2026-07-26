"""
Picks the active LLMProvider by name (form dropdown) or from the
REEL_MACHINE_LLM_PROVIDER env var, defaulting to the free template provider.

Construction failures (missing package, missing API key) fall back to the
template provider immediately rather than failing the whole job -- a
provider that starts working (network hiccup mid-call) is still handled by
pipeline.draft_script's own try/except around the actual call.
"""

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.llm.base import LLMProvider
from ofmhelpers.reel_machine.llm.template_provider import TemplateProvider

logger = get_logger(__name__)

PROVIDER_NAMES = ("template", "groq", "gemini", "anthropic")


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.reel_machine.llm_provider
    if name not in PROVIDER_NAMES:
        name = "template"

    if name == "groq":
        try:
            from ofmhelpers.reel_machine.llm.groq_provider import GroqProvider

            return GroqProvider()
        except Exception:
            logger.warning(
                "%s provider unavailable, falling back to template",
                "groq",
                exc_info=True,
            )
            return TemplateProvider()

    if name == "gemini":
        try:
            from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider

            return GeminiProvider()
        except Exception:
            logger.warning(
                "%s provider unavailable, falling back to template",
                "gemini",
                exc_info=True,
            )
            return TemplateProvider()

    if name == "anthropic":
        try:
            from ofmhelpers.reel_machine.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider()
        except Exception:
            logger.warning(
                "%s provider unavailable, falling back to template",
                "anthropic",
                exc_info=True,
            )
            return TemplateProvider()

    return TemplateProvider()
