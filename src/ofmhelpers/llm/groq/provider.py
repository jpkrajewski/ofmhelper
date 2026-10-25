"""GroqCloud's OpenAI-compatible API (console.groq.com/docs, 2026-10-24).

Vision model `qwen/qwen3.8-27b` (`OFM_GROQ_MODEL`): images only, at most 3 a
request, strict JSON Schema. Asked with `reasoning_effort: none`: at the
default effort it answered a plain "Hola" to a JSON-schema request (400
`json_validate_failed`, 2026-10-25), and thinking would spend the free
plan's tokens. Free plan, per organization: 30
requests/min, 1,000/day, 8K tokens/min. Key: https://console.groq.com/keys
(`GROQ_API_KEY`). Inference data is not kept by default; Zero Data
Retention is a switch in the console's Data Controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from ofmhelpers.config import settings
from ofmhelpers.llm.base import register_llm
from ofmhelpers.llm.chat_completions import ChatCompletionsLLM

if TYPE_CHECKING:
    import httpx


@register_llm
class GroqLLM(ChatCompletionsLLM):
    name = "groq"
    label = "Groq"
    base_url = "https://api.groq.com/openai/v1"
    max_images = 3
    extra_body: ClassVar[dict[str, Any]] = {"reasoning_effort": "none"}

    @classmethod
    def default_key(cls) -> str | None:
        key = settings.llm.groq_api_key
        return key.get_secret_value() if key else None

    @classmethod
    def default_model(cls) -> str:
        return settings.llm.groq_model

    @classmethod
    def fake_transport(cls) -> httpx.BaseTransport:
        from ofmhelpers.llm.groq.fake import fake_groq

        return fake_groq().transport()
