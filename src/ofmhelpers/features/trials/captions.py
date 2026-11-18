"""AI captions for trial reels: the model watches the reel (Gemini's free
tier reads real video) -- transcript plus what happens -- then a second ask
turns that into five short, funny, viral Snapchat-style captions for the person
to choose from (one ask: five asks would cost five of the day's requests).
Watching walks `settings.llm.video_chain` like the clone AI check (a provider
down or out of quota hands over to the next entry) and gets a small proxy of
the reel (`media.ai_proxy`: inline, no upload); the captions are text only,
so they ask the chat provider first (Groq, 1,000 a day free) and only then
the video chain -- Gemini's free tier is 20 requests a day per model,
shared with /clone-links and prod (llm/CLAUDE.md)."""

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
{"transcript": "<what is said, verbatim or empty>", "description": "<what happens, one or two plain sentences>"}"""

_CAPTIONS = """A reel from a creator's phone: {description}
Spoken: {transcript}

Write {choices} different Snapchat captions for it, as JSON: {{"captions": ["<first>", "<second>", ...]}}
Rules for each: at most 7 words; lower case; funny, teasing, scroll-stopping;
the voice of the person in the reel, never a commentator; no hashtags, no
emojis, no quotes. Each a different idea, not the same joke reworded."""

# The prompt asks for teasing; what comes back is checked word by word, and a
# caption with a crude word is dropped (owner 2026-11-17: "my dick is out bro"
# and the like were on prod copies; "check for nsfw word after generation").
_WORD = re.compile(r"[a-z]+")


@lru_cache(maxsize=1)
def blocked_words() -> frozenset[str]:
    """Whole words no caption may hold (`blocked_words.json`, lower case)."""
    raw = Path(__file__).with_name("blocked_words.json").read_text(encoding="utf-8")
    return TypeAdapter(frozenset[str]).validate_json(raw)


def is_safe(caption: str) -> bool:
    """No blocked word in it, matched as a whole word: "assault" stays."""
    return blocked_words().isdisjoint(_WORD.findall(caption.casefold()))


class ReelNotes(BaseModel):
    transcript: str = ""
    description: str = ""


class Captions(BaseModel):
    """The choices, each one clean line that fits the bar, none twice, none
    with a blocked word; the first `CHOICES`. None at all is the wrong
    answer: the next model is asked."""

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


def _ask[A: BaseModel](
    prompt: Prompt, answer: type[A], chain: list[tuple[LLMUse, str]]
) -> A:
    """The first chain entry that answers; every entry's error otherwise.
    A video prompt skips entries that cannot watch video."""
    errors: list[str] = []
    for use, entry in chain:
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


def _video_chain() -> list[tuple[LLMUse, str]]:
    return [(LLMUse.VIDEO, e) for e in settings.llm.video_chain]


def understand(video: Path) -> ReelNotes:
    """Transcript + what happens, from watching (a proxy of) the reel."""
    prompt = Prompt.of(_NOTES, media.ai_proxy(video), system=_RULES)
    return _ask(prompt, ReelNotes, _video_chain())


def captions_for(notes: ReelNotes) -> list[str]:
    """Up to `CHOICES` viral captions, at least one, the model's best first."""
    prompt = _CAPTIONS.format(
        description=notes.description or "(no description)",
        transcript=notes.transcript or "(nothing said)",
        choices=CHOICES,
    )
    chain = [(LLMUse.CHAT, settings.llm.default_chat), *_video_chain()]
    return _ask(Prompt.of(prompt, system=_RULES), Captions, chain).captions
