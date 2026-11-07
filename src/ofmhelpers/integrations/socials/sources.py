"""Where an account's numbers come from, whatever the platform: every way of
reading one (a scraping API, our own client through proxies, our client
logged in) is an `AccountSource`, and `scan_through` asks them in turn.

One source answers what it can for a whole batch and passes the rest on:
keys out of credit, a run cut short, rate-limited proxies -- whatever a
source did not read goes to the next one, so half an answer is never the
end. A platform lists its sources in order (`Platform.sources`); a new
platform or a new scraper is one more source, the tracker never changes.

Everything is keyed by `ScanTarget.key` (the tracked account), never by the
handle: two users may track one handle, and one's login must never read the
other's row."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

from pydantic import BaseModel, Field

from ofmhelpers.integrations.socials.models import AccountStats
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class ScanTarget(BaseModel):
    """One account to read. `key`: whose row the answer is for (the tracked
    account's id). `cookies`: its owner's own login (cookies.txt text), only
    for a source that logs in; None: they have none."""

    key: str
    handle: str
    cookies: str | None = Field(default=None, repr=False)


class Refusal(BaseModel):
    """Why a source could not read an account. `reached`: the platform itself
    answered (a lasting refusal, "not found"); False: the source never got
    there (no keys, no credit, proxies down) -- not the account's fault."""

    message: str
    reached: bool = True


class Answers(BaseModel):
    """What sources made of a batch, by `ScanTarget.key`: each read account's
    stats, and for the rest the last refusal heard (an account in neither:
    nobody got to it)."""

    stats: dict[str, AccountStats] = {}
    refusals: dict[str, Refusal] = {}


class AccountSource(ABC):
    # Stored with each scan (`AccountMetrics.source`).
    key: ClassVar[str]

    @abstractmethod
    def scan(self, targets: list[ScanTarget]) -> Answers:
        """Reads as many of `targets` as it can. Never raises for one account
        or for the source being unusable: what it did not read is left out of
        `stats` (with a `Refusal` when it knows why) for the next source."""


def scan_through(
    sources: list[AccountSource],
    targets: list[ScanTarget],
    on_read: Callable[[str, AccountStats], None],
) -> Answers:
    """`targets` through `sources` in order: each gets only what the ones
    before it did not read. `on_read(key, stats)` as soon as a source has
    answered -- a paid read is saved before the next source starts, so a job
    killed later never pays for it again. A source that breaks anyway (a
    bug, an answer nobody expected) is logged and skipped, its batch passed
    on whole."""
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
            # The platform's own answer outlives a later "never got there".
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
