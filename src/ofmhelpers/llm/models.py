"""What a caller asks a model: a `Prompt` of text and media parts, in order.

Provider-neutral on purpose: a part is text or a local file of a known
kind, never a provider's upload handle or URL. Each provider turns parts
into its own wire format (inline bytes, an uploaded file) and says which
kinds it reads (`LLMProvider.media`).
"""

import mimetypes
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field

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
    # 0: the same input gives (nearly) the same answer -- a checker, not a
    # writer.
    temperature: float = Field(default=0.0, ge=0, le=2)

    @classmethod
    def of(cls, *items: str | Path, system: str = "", temperature: float = 0.0) -> Self:
        """Text for a `str`, a media file for a `Path`, in this order."""
        parts: list[TextPart | MediaPart] = [
            TextPart(text=i) if isinstance(i, str) else MediaPart.of(i) for i in items
        ]
        return cls(parts=parts, system=system, temperature=temperature)

    @property
    def media(self) -> list[MediaPart]:
        return [p for p in self.parts if isinstance(p, MediaPart)]
