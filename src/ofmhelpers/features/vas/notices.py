from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import days, get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, User
from ofmhelpers.features.events import Event, emit
from ofmhelpers.features.vas.service import REEL_TASKS
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.jobs.schema import Job, UserDay

logger = get_logger(__name__)

PAGE = "/vas"


def _whose(va: User) -> str:
    owner = users.get_user(va.owner_id) if va.owner_id else None
    return f"VA of {owner.label}" if owner is not None else "VA"


def logged_in(user: User) -> None:
    if user.role != Role.VA:
        return
    emit(
        Event(
            name="vas.login",
            title=f"{user.label} logged in",
            text=_whose(user),
            url=PAGE,
            owner_id=user.owner_id,
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
        owner_id=va.owner_id,
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
        owner_id=va.owner_id,
    )


def _milestones(job: Job, va: User) -> list[Event]:
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
    try:
        job = get_job(job_id)
        va = users.get_user(job.user_id) if job and job.user_id else None
        if job is None or va is None or va.role != Role.VA:
            return
        for event in _milestones(job, va):
            emit(event)
    except Exception:
        logger.warning("va notices of job %s not made", job_id, exc_info=True)
