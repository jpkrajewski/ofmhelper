"""Cerebras Inference's OpenAI-compatible API (inference-docs.cerebras.ai,
2026-10-24).

Vision model `qwen-3.8-27b` (`OFM_CEREBRAS_MODEL`): images only, at most 2
and 10 MiB a request, strict JSON Schema. Free Trial tier: 5 requests/min,
1M tokens/day. Key: https://cloud.cerebras.ai (`CEREBRAS_API_KEY`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.llm.base import register_llm
from ofmhelpers.llm.chat_completions import ChatCompletionsLLM

if TYPE_CHECKING:
    import httpx


@register_llm
class CerebrasLLM(ChatCompletionsLLM):
    name = "cerebras"
    label = "Cerebras"
    base_url = "https://api.cerebras.ai/v1"
    max_images = 2

    @classmethod
    def default_key(cls) -> str | None:
        key = settings.llm.cerebras_api_key
        return key.get_secret_value() if key else None

    @classmethod
    def default_model(cls) -> str:
        return settings.llm.cerebras_model

    @classmethod
    def fake_transport(cls) -> httpx.BaseTransport:
        from ofmhelpers.llm.cerebras.fake import fake_cerebras

        return fake_cerebras().transport()
