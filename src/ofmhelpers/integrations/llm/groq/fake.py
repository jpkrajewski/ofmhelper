# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.integrations.llm.chat_completions_fake import FakeChatCompletions

FAKE_KEY = "fake-groq-key"
HOST = "api.groq.com"
MODELS = frozenset(
    {
        "qwen/qwen3.8-27b",
        "meta-models/muse-spark-open",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    },
)


@lru_cache(maxsize=1)
def fake_groq() -> FakeChatCompletions:
    return FakeChatCompletions(HOST, FAKE_KEY, MODELS, max_images=3)
