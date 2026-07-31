"""
Picks the active providers by name or from the environment: the video one
(REEL_MACHINE_LLM_PROVIDER, default Gemini -- the only free API that takes
video) and the text one for the second pass (REEL_MACHINE_TEXT_PROVIDER,
default Groq).

No fallback provider: an unknown name or a missing API key raises, which
fails the job with "GEMINI_API_KEY" rather than quietly producing a worse
prompt from a stand-in nobody chose.

`get_text_provider` is the one deliberate exception, and only for a missing
key: pass 2 is a nice-to-have that runs *after* the download and the analysis
(see `hunt.py`), so "nobody configured it" answers None and the caller falls
back to search terms derived from the analysis itself. An unknown *name* is a
typo in the deployment and still raises.
"""

from collections.abc import Callable

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.llm import LLMProvider, TextLLMProvider
from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider
from ofmhelpers.reel_machine.llm.groq_provider import GroqProvider

logger = get_logger(__name__)

PROVIDERS: dict[str, Callable[[], LLMProvider]] = {
    GeminiProvider.name: GeminiProvider,
}
DEFAULT_PROVIDER = GeminiProvider.name

TEXT_PROVIDERS: dict[str, Callable[[], TextLLMProvider]] = {
    GroqProvider.name: GroqProvider,
}
DEFAULT_TEXT_PROVIDER = GroqProvider.name


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.reel_machine.llm_provider or DEFAULT_PROVIDER
    try:
        factory = PROVIDERS[name]
    except KeyError:
        msg = f"unknown provider {name!r} (expected one of {', '.join(PROVIDERS)})"
        raise ValueError(msg) from None
    return factory()


def get_text_provider(name: str | None = None) -> TextLLMProvider | None:
    """The second-pass provider, or None when it has no key configured."""
    name = name or settings.reel_machine.text_llm_provider or DEFAULT_TEXT_PROVIDER
    try:
        factory = TEXT_PROVIDERS[name]
    except KeyError:
        msg = (
            f"unknown text provider {name!r} "
            f"(expected one of {', '.join(TEXT_PROVIDERS)})"
        )
        raise ValueError(msg) from None
    try:
        return factory()
    except KeyError as exc:
        logger.info("no %s configured, skipping the second pass", exc.args[0])
        return None
