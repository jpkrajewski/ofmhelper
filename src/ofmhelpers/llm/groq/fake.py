"""Groq faked at the wire (see `llm/chat_completions_fake.py`)."""

from functools import lru_cache

from ofmhelpers.llm.chat_completions_fake import FakeChatCompletions

FAKE_KEY = "fake-groq-key"
HOST = "api.groq.com"
# Models with strict JSON Schema (console.groq.com/docs/structured-outputs,
# 2026-10-24); the first two read images.
MODELS = frozenset(
    {
        "qwen/qwen3.8-27b",
        "meta-models/muse-spark-open",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    }
)


@lru_cache(maxsize=1)
def fake_groq() -> FakeChatCompletions:
    return FakeChatCompletions(HOST, FAKE_KEY, MODELS, max_images=3)
