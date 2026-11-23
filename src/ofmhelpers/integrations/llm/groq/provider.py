# Copyright (C) 2026 jpkrajewski

from typing import Any, ClassVar

lazy import httpx

from ofmhelpers.config import settings
from ofmhelpers.integrations.llm.base import register_llm
from ofmhelpers.integrations.llm.chat_completions import ChatCompletionsLLM
lazy from ofmhelpers.integrations.llm.groq.fake import fake_groq


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
        return fake_groq().transport()
