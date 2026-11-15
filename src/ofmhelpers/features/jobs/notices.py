"""The owner is told on Telegram when a provider has no money left (owner
2026-11-15: "notification on telegram that there was a No credits error so i
can act fast - any fail related to no money from providers").

One event on the bus (`features/events`, today: Telegram):

- `provider.no_credits`: a job failed because a provider's account is empty
  (`ProviderNoCreditsError`: kie.ai, Higgsfield, RunningHub, Elyum, APIYI;
  `ApifyCreditError`: Apify).

Sent from the two places a provider failure is recorded: `run_job`
(`features/jobs/runner.py`) and recovery (`features/jobs/recovery.py`). Its
key names the provider and the hour (`settings.web.no_credits_notice_every_s`),
so a batch failing on an empty account sends one message, and another an
hour later while it is still empty. Best effort: a notice that cannot be made
is logged with its trace and never fails the job."""

import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.events import Event, emit
from ofmhelpers.integrations.ai_providers.errors import ProviderNoCreditsError
from ofmhelpers.integrations.apify import ApifyCreditError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.errors import error_line

logger = get_logger(__name__)

# Where the keys are kept: a topped-up or new key goes in there.
PAGE = "/account"


def money_provider(exc: BaseException) -> str | None:
    """The provider whose empty account `exc` reports, else None."""
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


def out_of_money(job_id: str, provider: str, error: str) -> None:
    """`provider` refused `job_id` for want of money: told once per provider
    per `no_credits_notice_every_s`. Never raises."""
    try:
        every = settings.web.no_credits_notice_every_s
        emit(
            Event(
                name="provider.no_credits",
                title=f"{provider}: no credits",
                text=f"{_who(job_id)}: {error_line(error)}",
                url=PAGE,
                key=f"provider.no_credits:{provider}:{int(time.time() // every)}",
            )
        )
    except Exception:
        logger.warning("no-credits notice of job %s not made", job_id, exc_info=True)
