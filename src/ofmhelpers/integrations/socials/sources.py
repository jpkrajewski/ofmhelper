from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

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


class AccountSource(ABC):
    key: ClassVar[str]

    @abstractmethod
    def scan(self, targets: list[ScanTarget]) -> Answers: ...


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
        try:
            answers = source.scan(left)
        except Exception:
            logger.exception("%s source failed on %d accounts", source.key, len(left))
            continue
        for key, stats in answers.stats.items():
            read = stats.model_copy(update={"source": source.key})
            found.stats[key] = read
            try:
                on_read(key, read)
            except Exception:
                logger.exception("saving %s's read of %s failed", source.key, key)
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
