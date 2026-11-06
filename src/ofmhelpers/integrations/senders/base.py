"""What every sender (Telegram, ...) offers, and how it fails.

A sender takes an `Event` to one target at a time: a Telegram chat, later a
Discord webhook, an email address. `targets()` are where its settings say events
go; none = not set up, and the aggregator (features/events/dispatch.py) skips it.
Each (sender, target) is its own queued job, so one dead target never holds or
repeats the others. `pace_s(target)`: the least time between two sends to one
target (the service's own rate limit); delivery waits it out instead of being
refused. A message an event `replaces` is deleted with `delete(target, ref)`, `ref`
being what `send` returned for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ofmhelpers.errors import AppError

if TYPE_CHECKING:
    from ofmhelpers.schemas.events import Event


class SendError(AppError):
    """The service refused or failed the send; the message never holds a
    credential or a target. `retry_after`: the service asked to wait this many
    seconds first (a rate limit). `permanent`: trying again cannot help (the
    target is gone, the bot was removed): delivery gives up at once."""

    def __init__(
        self, message: str, *, retry_after: float | None = None, permanent: bool = False
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class Sender(Protocol):
    """One service events go out through. `name` is what `OFM_EVENTS_SENDERS_OFF`
    and the spans call it."""

    name: str

    def targets(self) -> list[str]:
        """Where events go; empty when not set up (no token, no chat)."""
        ...

    def pace_s(self, target: str) -> float:
        """Least seconds between two sends to `target`."""
        ...

    def send(self, target: str, event: Event) -> str | None:
        """The sent message's reference (what `delete` takes), None when the
        service gives none. Raises `SendError`."""
        ...

    def delete(self, target: str, ref: str) -> None:
        """Takes back a message it sent (`Event.replaces`). Raises `SendError`."""
        ...
