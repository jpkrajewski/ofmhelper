"""What a caller asks a model -- a `Prompt` of text and media parts, in
order -- and what one attempt brings back (`Reply`: the text and its
`Usage`).

Provider-neutral on purpose: a part is text or a local file of a known
kind, never a provider's upload handle or URL. Each provider turns parts
into its own wire format (inline bytes, an uploaded file) and says which
kinds it reads (`LLMProvider.media`).
"""

import mimetypes
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.llm.errors import LLMInputError


class LLMUse(StrEnum):
    """The kinds of work a default provider is configured for
    (`LLM_DEFAULT_VIDEO`, `LLM_DEFAULT_CHAT`)."""

    VIDEO = "video"
    CHAT = "chat"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class MediaPart(BaseModel):
    """A local file the model reads: an image, a video or audio."""

    type: Literal["media"] = "media"
    path: Path
    mime_type: str
    kind: MediaKind

    @classmethod
    def of(cls, path: Path) -> Self:
        """`path` with its type told from the file name. Raises
        `LLMInputError` for a file that is not an image, video or audio."""
        mime = mimetypes.guess_type(path.name)[0] or ""
        family = mime.split("/", 1)[0]
        if family not in MediaKind.__members__.values():
            msg = f"{path.name} is not an image, video or audio file"
            raise LLMInputError(msg)
        return cls(path=path, mime_type=mime, kind=MediaKind(family))


Part = Annotated[TextPart | MediaPart, Field(discriminator="type")]


class Prompt(BaseModel):
    """One request: `system` sets the model's role and rules, `parts` are
    the user's turn in the order the model sees them."""

    parts: list[Part] = Field(min_length=1)
    system: str = ""
    # None: the provider's own default (`LLMProvider.temperature`).
    temperature: float | None = Field(default=None, ge=0, le=2)

    @classmethod
    def of(
        cls, *items: str | Path, system: str = "", temperature: float | None = None
    ) -> Self:
        """Text for a `str`, a media file for a `Path`, in this order."""
        parts: list[TextPart | MediaPart] = [
            TextPart(text=i) if isinstance(i, str) else MediaPart.of(i) for i in items
        ]
        return cls(parts=parts, system=system, temperature=temperature)

    @property
    def media(self) -> list[MediaPart]:
        return [p for p in self.parts if isinstance(p, MediaPart)]


class Usage(BaseModel):
    """The tokens an answer took, as the provider counted them (None: not
    reported). Thinking is billed as output where a model thinks."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None

    @classmethod
    def summed(cls, usages: Iterable[Self]) -> Self:
        """Every count added up; a count no usage reported stays None."""
        found = list(usages)

        def total(name: str) -> int | None:
            counts = [n for u in found if (n := getattr(u, name)) is not None]
            return sum(counts) if counts else None

        return cls(**{name: total(name) for name in cls.model_fields})


class Reply(BaseModel):
    """One attempt's answer: the model's JSON text and what it took."""

    model_config = ConfigDict(frozen=True)

    text: str
    usage: Usage = Usage()
