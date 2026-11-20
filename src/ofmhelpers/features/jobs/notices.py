import time

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.db.users import store as users
from ofmhelpers.features.events import Event, emit
from ofmhelpers.features.jobs.schema import error_line
from ofmhelpers.integrations.ai_providers.errors import ProviderNoCreditsError
from ofmhelpers.integrations.apify import ApifyCreditError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

PAGE = "/account"


def money_provider(exc: BaseException) -> str | None:
    if isinstance(exc, (ProviderNoCreditsError, ApifyCreditError)):
        return exc.provider
    return None


def _who(job_id: str) -> str:
    job = get_job(job_id)
    if job is None:
        return f"job {job_id[:8]}"
    user = users.get_user(job.user_id) if job.user_id else None
    who = user.label if user is not None else "someone"
    return f"{who} · {job.task}"


def _key_owner(job_id: str) -> int | None:
    job = get_job(job_id)
    user = users.get_user(job.user_id) if job and job.user_id else None
    if user is None:
        return None
    return user.owner_id if user.role.has_employer else user.id


def out_of_money(job_id: str, provider: str, error: str) -> None:
    try:
        every = settings.web.no_credits_notice_every_s
        owner = _key_owner(job_id)
        emit(
            Event(
                name="provider.no_credits",
                title=f"{provider}: no credits",
                text=f"{_who(job_id)}: {error_line(error)}",
                url=PAGE,
                key=f"provider.no_credits:{provider}:{owner}:{int(time.time() // every)}",
                owner_id=owner,
            )
        )
    except Exception:
        logger.warning("no-credits notice of job %s not made", job_id, exc_info=True)
