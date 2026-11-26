# Copyright (C) 2026 jpkrajewski

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, field_validator

from ofmhelpers.config import settings
from ofmhelpers.features.trials import media
from ofmhelpers.features.trials.state import CAPTION_MAX
from ofmhelpers.integrations.llm import LLMError, LLMUse, MediaKind, Prompt, get_llm
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

CHOICES = 5

_RULES = (
    "You caption adult promo reels for social media. You are given a reel "
    "and answer strictly as JSON."
)

_NOTES = """Watch this reel and answer as JSON:
{"transcript": "<what is said, verbatim or empty>", "description": "<what "
    "happens, one or two plain sentences>"}"""

_CAPTIONS = """A reel from a creator's phone: {description}
Spoken: {transcript}

Write {choices} different Snapchat captions for it, as JSON: {{"captions": ["<first>", "<second>", ...]}}
Rules for each: at most 7 words; lower case; funny, teasing, scroll-stopping;
the voice of the person in the reel, never a commentator; no hashtags, no
emojis, no quotes. Each a different idea, not the same joke reworded."""

_WORD = re.compile(r"[a-z]+")


@lru_cache(maxsize=1)
def blocked_words() -> frozenset[str]:
    raw = Path(__file__).with_name("blocked_words.json").read_text(encoding="utf-8")
    return TypeAdapter(frozenset[str]).validate_json(raw)


def is_safe(caption: str) -> bool:
    return blocked_words().isdisjoint(_WORD.findall(caption.casefold()))


class ReelNotes(BaseModel):
    transcript: str = ""
    description: str = ""


class Captions(BaseModel):
    captions: list[str]

    @field_validator("captions")
    @classmethod
    def _clean(cls, found: list[str]) -> list[str]:
        cleaned = (" ".join(text.split()).strip('"').strip() for text in found)
        unique = {
            text.casefold(): text[:CAPTION_MAX]
            for text in cleaned
            if text and is_safe(text)
        }
        if not unique:
            msg = "no caption"
            raise ValueError(msg)
        return list(unique.values())[:CHOICES]


class _Step(BaseModel, frozen=True):
    use: LLMUse
    entry: str


def _ask[A: BaseModel](prompt: Prompt, answer: type[A], chain: list[_Step]) -> A:
    errors: list[str] = []
    for step in chain:
        use, entry = step.use, step.entry
        try:
            llm = get_llm(use, provider=entry)
            if prompt.media and MediaKind.VIDEO not in llm.media:
                continue
            return llm.ask(prompt, answer)
        except LLMError as exc:
            errors.append(f"{entry}: {type(exc).__name__}: {exc}")
            logger.warning("trials captions: %s failed: %s", entry, exc, exc_info=True)
    msg = "no model answered: " + "; ".join(errors)
    raise RuntimeError(msg)


def _video_chain() -> list[_Step]:
    return [_Step(use=LLMUse.VIDEO, entry=e) for e in settings.llm.video_chain]


def understand(video: Path) -> ReelNotes:
    prompt = Prompt.of([_NOTES, media.ai_proxy(video)], system=_RULES)
    return _ask(prompt, ReelNotes, _video_chain())


def captions_for(notes: ReelNotes) -> list[str]:
    prompt = _CAPTIONS.format(
        description=notes.description or "(no description)",
        transcript=notes.transcript or "(nothing said)",
        choices=CHOICES,
    )
    chain = [_Step(use=LLMUse.CHAT, entry=settings.llm.default_chat), *_video_chain()]
    return _ask(Prompt.of([prompt], system=_RULES), Captions, chain).captions
