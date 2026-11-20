lazy import httpx

from ofmhelpers.config import settings
from ofmhelpers.integrations.llm.base import register_llm
from ofmhelpers.integrations.llm.chat_completions import ChatCompletionsLLM


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
        from ofmhelpers.integrations.llm.cerebras.fake import fake_cerebras

        return fake_cerebras().transport()
