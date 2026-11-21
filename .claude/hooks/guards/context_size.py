import os
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from guards.event import SHIP_OPEN, Guard, HookEvent, Markers, Verdict, context

NAME = "context_size"
TELL_FROM = 400_000
TELL_EVERY = 200_000
CHUNK = 65_536
READ_LIMIT = 8 * 1024 * 1024
ASSISTANT = b'"type":"assistant"'
TELL = (
    "context is {k}k tokens; tell the owner in one line that a fresh session for a new task "
    "cuts cost"
)


class Usage(BaseModel):
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def context_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class Message(BaseModel):
    usage: Usage | None = None


class TranscriptLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = ""
    is_sidechain: bool = Field(default=False, alias="isSidechain")
    message: Message | None = None


def lines_from_the_end(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        position = end
        rest = b""
        while position > 0 and end - position < READ_LIMIT:
            step = min(CHUNK, position)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + rest).split(b"\n")
            rest = lines[0]
            yield from reversed(lines[1:])
        if position == 0:
            yield rest


def context_tokens(path: Path) -> int:
    for line in lines_from_the_end(path):
        if ASSISTANT not in line:
            continue
        try:
            parsed = TranscriptLine.model_validate_json(line)
        except ValidationError:  # silent: a half-written or foreign line is skipped
            continue
        usage = parsed.message.usage if parsed.message else None
        if parsed.type == "assistant" and not parsed.is_sidechain and usage:
            return usage.context_tokens
    return 0


def told_at(markers: Markers) -> int:
    told = markers.value("context-told").strip()
    return int(told) if told.isdigit() else 0


def check(event: HookEvent) -> Verdict | None:
    if not event.is_owner_prompt or SHIP_OPEN.is_file():
        return None
    transcript = Path(event.transcript_path)
    if not event.transcript_path or not transcript.is_file():
        return None
    tokens = context_tokens(transcript)
    if tokens < TELL_FROM:
        return None
    markers = Markers.of(event)
    told = told_at(markers)
    if told and told <= tokens < told + TELL_EVERY:
        return None
    markers.set("context-told", str(tokens))
    return context(NAME, TELL.format(k=tokens // 1000))


GUARD = Guard(name=NAME, events=frozenset({"UserPromptSubmit"}), check=check)
