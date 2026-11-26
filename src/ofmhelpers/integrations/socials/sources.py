# Copyright (C) 2026 jpkrajewski

from abc import ABC, abstractmethod
from typing import ClassVar
lazy from collections.abc import Callable

from pydantic import BaseModel, Field

from ofmhelpers.integrations.socials.models import AccountStats
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class ScanTarget(BaseModel):
    key: str
    handle: str
    cookies: str | None = Field(default=None, repr=False)


class Refusal(BaseModel):
    message: str
    reached: bool = True


class Answers(BaseModel):
    stats: dict[str, AccountStats] = {}
    refusals: dict[str, Refusal] = {}


type Landed = Callable[[str, AccountStats], None]


class AccountSource(ABC):
    key: ClassVar[str]

    @abstractmethod
    def scan(self, targets: list[ScanTarget]) -> Answers: ...

    def scan_saving(
        self,
        targets: list[ScanTarget],
        landed: Landed,  # noqa: ARG002
    ) -> Answers:
        return self.scan(targets)


class _Saver:
    def __init__(
        self,
        source: str,
        found: Answers,
        on_read: Landed,
        saved: set[str],
    ) -> None:
        self._source = source
        self._found = found
        self._on_read = on_read
        self._saved = saved

    def __call__(self, key: str, stats: AccountStats) -> None:
        read = stats.model_copy(update={"source": self._source})
        self._found.stats[key] = read
        self._saved.add(key)
        try:
            self._on_read(key, read)
        except Exception:
            logger.exception("saving %s's read of %s failed", self._source, key)


def scan_through(
    sources: list[AccountSource],
    targets: list[ScanTarget],
    on_read: Callable[[str, AccountStats], None],
) -> Answers:
    found = Answers()
    left = list(targets)
    for source in sources:
        if not left:
            break
        saved: set[str] = set()
        save = _Saver(source.key, found, on_read, saved)
        try:
            answers = source.scan_saving(left, save)
        except Exception:
            logger.exception("%s source failed on %d accounts", source.key, len(left))
            left = [t for t in left if t.key not in found.stats]
            continue
        for key, stats in answers.stats.items():
            if key not in saved:
                save(key, stats)
        for key, refusal in answers.refusals.items():
            if refusal.reached or key not in found.refusals:
                found.refusals[key] = refusal
        left = [t for t in left if t.key not in found.stats]
        logger.info(
            "%s source read %d accounts, %d left",
            source.key,
            len(answers.stats),
            len(left),
        )
    found.refusals = {k: r for k, r in found.refusals.items() if k not in found.stats}
    return found
