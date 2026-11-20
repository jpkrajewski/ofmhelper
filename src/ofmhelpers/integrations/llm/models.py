import mimetypes
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
lazy from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.integrations.llm.errors import LLMInputError


class LLMUse(StrEnum):
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
    type: Literal["media"] = "media"
    path: Path
    mime_type: str
    kind: MediaKind

    @classmethod
    def of(cls, path: Path) -> Self:
        mime = mimetypes.guess_type(path.name)[0] or ""
        family = mime.split("/", 1)[0]
        if family not in MediaKind.__members__.values():
            msg = f"{path.name} is not an image, video or audio file"
            raise LLMInputError(msg)
        return cls(path=path, mime_type=mime, kind=MediaKind(family))


Part = Annotated[TextPart | MediaPart, Field(discriminator="type")]


class Prompt(BaseModel):
    parts: list[Part] = Field(min_length=1)
    system: str = ""
    temperature: float | None = Field(default=None, ge=0, le=2)

    @classmethod
    def of(
        cls, *items: str | Path, system: str = "", temperature: float | None = None
    ) -> Self:
        parts: list[TextPart | MediaPart] = [
            TextPart(text=i) if isinstance(i, str) else MediaPart.of(i) for i in items
        ]
        return cls(parts=parts, system=system, temperature=temperature)

    @property
    def media(self) -> list[MediaPart]:
        return [p for p in self.parts if isinstance(p, MediaPart)]


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None

    @classmethod
    def summed(cls, usages: Iterable[Self]) -> Self:
        found = list(usages)

        def total(name: str) -> int | None:
            counts = [n for u in found if (n := getattr(u, name)) is not None]
            return sum(counts) if counts else None

        return cls(**{name: total(name) for name in cls.model_fields})


class Reply(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    usage: Usage = Usage()
