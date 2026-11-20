from functools import lru_cache

from ofmhelpers.integrations.llm.chat_completions_fake import FakeChatCompletions

FAKE_KEY = "fake-cerebras-key"
HOST = "api.cerebras.ai"
MODELS = frozenset({"qwen-3.8-27b", "gpt-oss-120b"})


@lru_cache(maxsize=1)
def fake_cerebras() -> FakeChatCompletions:
    return FakeChatCompletions(HOST, FAKE_KEY, MODELS, max_images=2)
