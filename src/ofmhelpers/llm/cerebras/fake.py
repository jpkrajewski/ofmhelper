"""Cerebras faked at the wire (see `llm/chat_completions_fake.py`)."""

from functools import lru_cache

from ofmhelpers.llm.chat_completions_fake import FakeChatCompletions

FAKE_KEY = "fake-cerebras-key"
HOST = "api.cerebras.ai"
# Shared Inference models (inference-docs.cerebras.ai, 2026-10-24); the
# first reads images.
MODELS = frozenset({"qwen-3.8-27b", "gpt-oss-120b"})


@lru_cache(maxsize=1)
def fake_cerebras() -> FakeChatCompletions:
    return FakeChatCompletions(HOST, FAKE_KEY, MODELS, max_images=2)
