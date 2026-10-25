"""Language models behind one interface:
`get_llm(LLMUse.VIDEO).ask(Prompt.of(...), Answer)`.
See llm/CLAUDE.md."""

from ofmhelpers.llm.base import (
    LLMProvider,
    default_for,
    get_llm,
    llm_class,
    llm_names,
    register_llm,
)
from ofmhelpers.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMRefusedError,
    LLMUnavailableError,
)
from ofmhelpers.llm.models import LLMUse, MediaKind, MediaPart, Prompt, TextPart

__all__ = [
    "LLMAuthError",
    "LLMConfigError",
    "LLMError",
    "LLMInputError",
    "LLMOutputError",
    "LLMProvider",
    "LLMRefusedError",
    "LLMUnavailableError",
    "LLMUse",
    "MediaKind",
    "MediaPart",
    "Prompt",
    "TextPart",
    "default_for",
    "get_llm",
    "llm_class",
    "llm_names",
    "register_llm",
]
