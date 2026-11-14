"""What the owner is told about a VA while the VA works (owner 2026-11-14:
"when va logs in we get notification which va logged in ... milestones when
va generated 10 reels ... when va spend more than 10$ ... and then 20, 30
ect so we can just check on them"). Three events on the bus
(`features/events`, today: Telegram):

- `vas.login`: a VA signed in (`features/signin/outcome.py`).
- `vas.reels`: a VA finished another 10 reels that day (10, 20, 30, ...).
- `vas.spend`: a VA's day passed another $10 (10, 20, 30, ...).

The two milestones are looked at when a job of a VA ends
(`features/jobs/runner.py` `run_job`, after its cost is kept): the day's
count and dollars are read fresh (`db/stores/jobs.py` `days`, the day of
`settings.reports.timezone`) and the step reached goes out under a key
naming the VA, the day and the step, so the bus sends each once however many
jobs end past it. Best effort: a notice that cannot be made is logged with
its trace and never fails the job.

A run with no known price (a /generate model whose provider reports none)
adds nothing to the dollars, as on /vas."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import days, get_job
from ofmhelpers.features.events import Event, emit
from ofmhelpers.features.vas.service import REEL_TASKS
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.users import Role, User
lazy from ofmhelpers.schemas.persistence import Job, UserDay

logger = get_logger(__name__)

PAGE = "/vas"


def _whose(va: User) -> str:
    owner = users.get_user(va.owner_id) if va.owner_id else None
    return f"VA of {owner.label}" if owner is not None else "VA"


def logged_in(user: User) -> None:
    """`user` just signed in: told when they are a VA."""
    if user.role != Role.VA:
        return
    emit(
        Event(
            name="vas.login",
            title=f"{user.label} logged in",
            text=_whose(user),
            url=PAGE,
        )
    )


def _today(va: User) -> UserDay | None:
    zone = ZoneInfo(settings.reports.timezone)
    start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    found = days([va.id], start.timestamp(), end.timestamp(), zone.key, REEL_TASKS)
    return found[0] if found else None


def _reels(va: User, day: UserDay) -> Event | None:
    every = settings.web.va_reels_notice_every
    reached = day.reels // every * every
    if not reached:
        return None
    return Event(
        name="vas.reels",
        title=f"{va.label}: {reached} reels today",
        text=_whose(va),
        url=PAGE,
        key=f"vas.reels:{va.id}:{day.day}:{reached}",
    )


def _spend(va: User, day: UserDay) -> Event | None:
    every = settings.web.va_spend_notice_every_usd
    reached = int(day.usd // every) * every if day.usd is not None else 0
    if not reached:
        return None
    return Event(
        name="vas.spend",
        title=f"{va.label}: over ${reached} spent today",
        text=f"{_whose(va)} · ${day.usd:,.2f} so far",
        url=PAGE,
        key=f"vas.spend:{va.id}:{day.day}:{reached}",
    )


def _milestones(job: Job, va: User) -> list[Event]:
    """The steps `va`'s day stands on after `job`: reels when it is a reel
    that finished, dollars when it cost something."""
    made_reel = job.task in REEL_TASKS and job.status == "done"
    if not made_reel and job.cost_usd is None:
        return []
    day = _today(va)
    if day is None:
        return []
    found = [_reels(va, day) if made_reel else None]
    found.append(_spend(va, day) if job.cost_usd is not None else None)
    return [event for event in found if event is not None]


def after_job(job_id: str) -> None:
    """A job ended: its VA's milestones, if it is a VA's. Never raises."""
    try:
        job = get_job(job_id)
        va = users.get_user(job.user_id) if job and job.user_id else None
        if job is None or va is None or va.role != Role.VA:
            return
        for event in _milestones(job, va):
            emit(event)
    except Exception:
        logger.warning("va notices of job %s not made", job_id, exc_info=True)
