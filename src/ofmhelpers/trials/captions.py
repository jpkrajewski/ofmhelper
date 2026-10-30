"""AI captions for trial reels: the model watches the reel (Gemini's free
tier reads real video) -- transcript plus what happens -- then a second ask
turns that into one short, funny, viral Snapchat-style caption. Watching
walks `settings.llm.video_chain` like the clone AI check (a provider down
or out of quota hands over to the next entry) and gets a small proxy of
the reel (`media.ai_proxy`: inline, no upload); the caption is text only,
so it asks the chat provider first (Groq, 1,000 a day free) and only then
the video chain -- Gemini's free tier is 20 requests a day per model,
shared with /clone-links and prod (llm/CLAUDE.md)."""

from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.llm import (
    LLMError,
    LLMUse,
    MediaKind,
    Prompt,
    get_llm,
)
from ofmhelpers.log import get_logger
from ofmhelpers.trials import media

logger = get_logger(__name__)

_RULES = (
    "You caption adult promo reels for social media. You are given a reel "
    "and answer strictly as JSON."
)

_NOTES = """Watch this reel and answer as JSON:
{"transcript": "<what is said, verbatim or empty>", "description": "<what happens, one or two plain sentences>"}"""

_CAPTION = """A reel from a creator's phone: {description}
Spoken: {transcript}

Write ONE Snapchat caption for it, as JSON: {{"caption": "<it>"}}
Rules: at most 7 words; lower case; funny, teasing, scroll-stopping; the
voice of the person in the reel, never a commentator; no hashtags, no
emojis, no quotes.{used}"""


class ReelNotes(BaseModel):
    transcript: str = ""
    description: str = ""


class Caption(BaseModel):
    caption: str


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
            logger.warning("trials captions: %s failed: %s", entry, exc)
    msg = "no model answered: " + "; ".join(errors)
    raise RuntimeError(msg)


def _video_chain() -> list[tuple[LLMUse, str]]:
    return [(LLMUse.VIDEO, e) for e in settings.llm.video_chain]


def understand(video: Path) -> ReelNotes:
    """Transcript + what happens, from watching (a proxy of) the reel."""
    prompt = Prompt.of(_NOTES, media.ai_proxy(video), system=_RULES)
    return _ask(prompt, ReelNotes, _video_chain())


def caption_for(notes: ReelNotes, used: list[str]) -> str:
    """One viral caption; `used` (already tried) are told to be avoided."""
    used_line = ""
    if used:
        used_line = " Already tried (do NOT reuse): " + "; ".join(used)
    prompt = _CAPTION.format(
        description=notes.description or "(no description)",
        transcript=notes.transcript or "(nothing said)",
        used=used_line,
    )
    chain = [(LLMUse.CHAT, settings.llm.default_chat), *_video_chain()]
    return _ask(Prompt.of(prompt, system=_RULES), Caption, chain).caption.strip()
