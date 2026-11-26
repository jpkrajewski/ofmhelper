# Copyright (C) 2026 jpkrajewski

from typing import Protocol, runtime_checkable

lazy from ofmhelpers.integrations.senders.schema import Event


class SendError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        permanent: bool = False,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


@runtime_checkable
class Sender(Protocol):
    name: str

    def targets(self) -> list[str]: ...

    def pace_s(self, target: str) -> float: ...

    def send(self, target: str, event: Event) -> str | None: ...

    def delete(self, target: str, ref: str) -> None: ...
