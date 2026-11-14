"""Development only: a made-up month of a VA's work, to look at /vas and the
galleries with (owner 2026-11-14: "i need fake VA data generations from many
provides on gen ai and clone and fake downalkods to see how it looks like").

    python -m ofmhelpers.features.vas.seed seed <va name or email>
    python -m ofmhelpers.features.vas.seed clear

Refused in production. Nothing is run and no provider is called: `seed`
writes job rows as they would have ended -- clones on every priced /clone
provider (kie.ai, Higgsfield, APIYI, ...), a failed clone, /generate runs,
downloads, and the day's login and logout (or idle logout) -- over today,
the last week and earlier this month. Each row copies the inputs and the
result files of a real finished job of the VA's team (the owner's or a
VA's), so its card shows a real picture or video; with no such job of a
kind, that kind is left out. A card plays the result
file itself until the preview backfill (`features/previews/backfill.py`, at
the sweeps' next start) makes its light copy.

Money is the app's own (`features/jobs/cost.py` `cost_of`): a clone's price
is the /clone table's for its provider, resolution and length (kie.ai's as
the provider would report it, the others as the app's estimate). A
/generate run is written for one of a few models with a known price
(`GENERATED`): what kie.ai reported for it on real runs, APIYI's published
price per picture, or the /clone table for Higgsfield's Wan 3.0 video. A
real run of an APIYI or Higgsfield model on /generate shows no price yet
(their APIs report none and the app has no price list for /generate): the
made-up ones show what such a run does cost. Higgsfield pictures are left
out: no price of theirs is known here.

Every row's id starts with `seed-`: `clear` deletes exactly those (their
download rows go with them) and their previews. Running `seed` again clears
first."""

import argparse
import random
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import add_recorded, delete_recorded, list_jobs_page
from ofmhelpers.features.clone.pricing import UnpricedError, clone_pricing
from ofmhelpers.features.clone.targets import clone_targets
from ofmhelpers.features.jobs.cost import cost_of
from ofmhelpers.features.previews.render import previews_dir
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.schemas.persistence import (
    GenerationRecord,
    Job,
    JobMeta,
    RecordedJob,
)
from ofmhelpers.schemas.users import Role, User

logger = get_logger(__name__)

PREFIX = "seed-"
# The days made up, as days before today: today, the last week, earlier.
DAYS_BACK = (0, 1, 2, 3, 5, 6, 9, 12, 16, 20)
RESOLUTION = "720P"
FLAGGED = "Flagged content: the provider refused this video"


class _Model(BaseModel):
    """A /generate model a made-up run is written for. `usd`: its price per
    run; None: the /clone table prices it by its length."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    usd: float | None = None


# kie.ai: as it reported on real runs (dev 2026-11-11); APIYI: per picture,
# docs.apiyi.com 2026-11-06 (integrations/ai_providers/apiyi/CLAUDE.md).
GENERATED = (
    _Model(provider="kie", model="kie:seedream/4.5-edit", usd=0.0325),
    _Model(provider="apiyi", model="apiyi:gemini-3-pro-image", usd=0.09),
    _Model(provider="apiyi", model="apiyi:gemini-3.1-flash-image", usd=0.055),
    _Model(
        provider="higgsfield",
        model="higgsfield:alibaba/wan-3.0/reference-to-video",
    ),
)
# How far back real jobs are looked through for ones to copy.
_SOURCES = 200
_HOUR = 3600.0


class Seeded(BaseModel):
    """What `seed` wrote, by kind."""

    va: str
    days: int
    clones: int = 0
    failed_clones: int = 0
    generations: int = 0
    downloads: int = 0
    events: int = 0


class Cleared(BaseModel):
    jobs: int
    previews: int


class _Sources(BaseModel):
    """Real finished jobs of the team to copy inputs and results from."""

    clones: list[Job]
    generations: list[Job]
    downloads: list[Job]


def _development_only() -> None:
    if settings.infra.production:
        msg = "Made-up data is for development only"
        raise RefusedError(msg)


def _va(name: str) -> User:
    wanted = name.strip().lower()
    for user in users.list_users():
        if user.role == Role.VA and wanted in (user.name.lower(), user.email.lower()):
            return user
    msg = f"No VA named {name}"
    raise NotFoundError(msg)


def _team(va: User) -> frozenset[str]:
    owner = va.owner_id or va.id
    vas = [str(v.id) for v in users.vas_of(owner)]
    return frozenset([str(owner), *vas])


def _real(task: str, team: frozenset[str]) -> list[Job]:
    """The team's newest finished jobs of `task` whose files are there,
    made-up ones left out."""
    found = list_jobs_page({task}, 0, _SOURCES, team, status="done").jobs
    return [job for job in found if not job.id.startswith(PREFIX) and job.result]


def _new_id() -> str:
    return PREFIX + uuid.uuid4().hex[:8]


def _generation(provider: str, model: str, usd: float | None) -> GenerationRecord:
    return GenerationRecord(
        provider=provider,
        model=model,
        task_id=f"seed-{uuid.uuid4()}",
        state="done",
        cost_usd=usd,
        seconds=180.0,
        key_tail="SEED",
    )


class _Work(BaseModel):
    """The rows one VA's made-up work is written as."""

    model_config = ConfigDict(frozen=True)

    va: User

    def _row(self, task: str, at: float, source: Job, meta: JobMeta) -> RecordedJob:
        cost = cost_of(source.params, meta.generations)
        return RecordedJob(
            id=_new_id(),
            task=task,
            user_id=self.va.id,
            created_at=at,
            finished_at=at + 240,
            params=source.params,
            result=source.result,
            meta=meta,
            cost_usd=cost.usd,
            cost_estimated=cost.estimated,
        )

    def clone(self, at: float, source: Job, provider: str, seconds: int) -> RecordedJob:
        """A finished clone of `source`'s video on `provider`, priced by the
        /clone table. kie.ai reports what a run cost, so its row carries the
        amount as reported; the others get the app's estimate."""
        target = clone_targets().entries[provider]
        price = clone_pricing().entries[provider]
        params = {
            **source.params,
            "provider": provider,
            "model": target.model,
            "resolution": RESOLUTION,
            "duration": seconds,
        }
        per_second = price.per_second(RESOLUTION) * price.usd_per_credit
        reported = per_second * seconds if provider == "kie" else None
        meta = JobMeta(generations=[_generation(provider, target.model, reported)])
        return self._row(
            "clone", at, source.model_copy(update={"params": params}), meta
        )

    def failed(self, at: float, source: Job) -> RecordedJob:
        return RecordedJob(
            id=_new_id(),
            task="clone",
            user_id=self.va.id,
            status="failed",
            created_at=at,
            finished_at=at + 60,
            params=source.params,
            error=FLAGGED,
        )

    def generated(
        self, at: float, source: Job, model: _Model, seconds: int
    ) -> RecordedJob:
        """A /generate run of `model`, shown with `source`'s result file:
        its price per run, or (none) the /clone table's for `seconds`."""
        params = {**source.params, "provider": model.provider, "model": model.model}
        if model.usd is None:
            params |= {"resolution": RESOLUTION, "duration": seconds}
        made = _generation(model.provider, model.model, model.usd)
        return self._row(
            source.task,
            at,
            source.model_copy(update={"params": params}),
            JobMeta(generations=[made]),
        )

    def download(self, at: float, source: Job) -> RecordedJob:
        return RecordedJob(
            id=_new_id(),
            task=source.task,
            user_id=self.va.id,
            created_at=at,
            finished_at=at + 20,
            params=source.params,
            result=source.result,
        )

    def event(self, task: str, at: float) -> RecordedJob:
        params = {"email": self.va.email, "with": "va"} if task == "login" else {}
        return RecordedJob(
            id=_new_id(),
            task=task,
            user_id=self.va.id,
            created_at=at,
            finished_at=at,
            params=params,
        )

    def day(
        self, start: float, sources: _Sources, rng: random.Random, back: int
    ) -> list[RecordedJob]:
        """One working day from `start` (its local midnight): login around
        nine, clones through the day, a few generations and downloads, then
        Logout -- or, every other day, nothing: the app logs them out idle."""
        at = start + 9 * _HOUR + rng.randint(0, 40) * 60
        rows = [self.event("login", at)]
        providers = _priced_providers()
        for number in range(rng.randint(4, 8) if sources.clones else 0):
            at += rng.randint(12, 45) * 60
            source = rng.choice(sources.clones)
            provider = providers[(back + number) % len(providers)]
            rows.append(self.clone(at, source, provider, rng.randint(5, 14)))
        if sources.clones and back % 2 == 0:
            at += 10 * 60
            rows.append(self.failed(at, rng.choice(sources.clones)))
        for number in range(2 if sources.generations else 0):
            at += rng.randint(5, 20) * 60
            source = rng.choice(sources.generations)
            model = GENERATED[(back + number) % len(GENERATED)]
            rows.append(self.generated(at, source, model, rng.randint(5, 10)))
        for source in rng.sample(sources.downloads, min(2, len(sources.downloads))):
            at += rng.randint(2, 10) * 60
            rows.append(self.download(at, source))
        left = "idle_logout" if back % 2 else "logout"
        rows.append(self.event(left, at + rng.randint(5, 30) * 60))
        return rows


def _priced_providers() -> list[str]:
    """The /clone providers with a price at `RESOLUTION`, in table order."""
    found = []
    for name, price in clone_pricing().entries.items():
        try:
            price.per_second(RESOLUTION)
        except UnpricedError:
            continue
        found.append(name)
    return found


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.reports.timezone)


def _rows(va: User, sources: _Sources, now: datetime) -> list[RecordedJob]:
    """Every made-up row up to `now`: today's day stops where the clock is,
    with no logout yet when its end is still ahead."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows: list[RecordedJob] = []
    for back in DAYS_BACK:
        rng = random.Random(f"{va.id}:{back}")  # noqa: S311 -- made-up data
        start = (midnight - timedelta(days=back)).timestamp()
        rows += _Work(va=va).day(start, sources, rng, back)
    return [row for row in rows if row.created_at <= now.timestamp()]


def _count(rows: list[RecordedJob], va: User) -> Seeded:
    clones = [r for r in rows if r.task == "clone"]
    events = [r for r in rows if r.task in ("login", "logout", "idle_logout")]
    downloads = [r for r in rows if r.task.startswith("download")]
    return Seeded(
        va=va.label,
        days=len({r.created_at // 86400 for r in events}),
        clones=sum(1 for r in clones if r.status == "done"),
        failed_clones=sum(1 for r in clones if r.status == "failed"),
        generations=len(rows) - len(clones) - len(events) - len(downloads),
        downloads=len(downloads),
        events=len(events),
    )


def seed(name: str, now: datetime | None = None) -> Seeded:
    """A made-up month of the VA `name` (their name or email), replacing
    the one made before. `now`: the moment it runs up to (default: this
    one)."""
    _development_only()
    va = _va(name)
    clear()
    team = _team(va)
    sources = _Sources(
        clones=_real("clone", team),
        generations=_real("ai", team),
        downloads=_real("download_media", team),
    )
    rows = _rows(va, sources, now or datetime.now(_zone()))
    add_recorded(rows)
    found = _count(rows, va)
    logger.info("made-up history for %s: %s", va.label, found.model_dump())
    return found


def clear() -> Cleared:
    """Every made-up row and its previews."""
    _development_only()
    gone = delete_recorded(PREFIX)
    previews = [p for p in previews_dir().glob(f"{PREFIX}*") if p.is_file()]
    for path in previews:
        path.unlink(missing_ok=True)
    return Cleared(jobs=gone, previews=len(previews))


def _main() -> None:
    parser = argparse.ArgumentParser(description="A VA's made-up history (dev).")
    commands = parser.add_subparsers(dest="command", required=True)
    seeding = commands.add_parser("seed", help="write a made-up month for a VA")
    seeding.add_argument("va", help="the VA's name or email")
    commands.add_parser("clear", help="delete every made-up row")
    args = parser.parse_args()
    configure_logging()
    if args.command == "seed":
        print(seed(args.va).model_dump_json())
    else:
        print(clear().model_dump_json())


if __name__ == "__main__":
    _main()
