"""A stored job run again against the fake providers, to see what it does:
`OFM_FAKE_EXTERNALS=true python -m ofmhelpers.features.jobs.replay <job_id>`
in the app container on dev (owner 2026-11-15: "can we replay any job with a
fake provider, a fake client?"; CLAUDE.md: a prod failure is proven fixed by
replaying THAT job on dev, prod data in dev first: `scripts/prod_to_dev.sh`).

The job runs here, in this process, through `run_job` (its row says done
or failed, its trace is in Logfire), never on a queue: a worker does not
run with the fakes, so a queued replay could pay a real provider. Refused
unless every outside service is faked and this is not production.

What runs, from the row alone:
- a local task recovery restarts (`registry.RESTARTS`): as it would restart;
- a paid one (`registry.RESUMABLE`): its params, the fake key of its
  provider, its person's store, as recovery re-runs it;
- a /trials job: `trials.folder.replay_run` (its `args`, its person's token);
- anything else: `NotReplayableError`, saying why (`NOT_REPLAYABLE`).
Files a job read (uploads, sources) must be on this disk: `prod_to_dev.sh`
copies the database only."""

import argparse
from typing import Any
lazy from collections.abc import Callable

from pydantic import BaseModel, SkipValidation

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import get_job, reopen_settled
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.generate.legacy import legacy_tools
from ofmhelpers.features.jobs.registry import RESTARTS, RESUMABLE
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.trials import folder as trials
from ofmhelpers.integrations.ai_providers.apiyi import fake as apiyi_fake
from ofmhelpers.integrations.ai_providers.elyum import fake as elyum_fake
from ofmhelpers.integrations.ai_providers.higgsfield import fake as higgsfield_fake
from ofmhelpers.integrations.ai_providers.kie import fake as kie_fake
from ofmhelpers.integrations.ai_providers.runninghub import fake as runninghub_fake
from ofmhelpers.log import configure_logging
from ofmhelpers.telemetry import configure_telemetry
lazy from ofmhelpers.schemas import Job

# The key each fake provider takes (a paid job's own key stays where it is).
FAKE_KEYS: frozendict[str, str] = frozendict(
    {
        "apiyi": apiyi_fake.FAKE_KEY,
        "elyum": elyum_fake.FAKE_KEY,
        "higgsfield": higgsfield_fake.FAKE_KEY,
        "kie": kie_fake.FAKE_KEY,
        "runninghub": runninghub_fake.FAKE_KEY,
        "fake": "",
    }
)
# Tasks that do not run again from their row, and why.
NOT_REPLAYABLE: frozendict[str, str] = frozendict(
    {
        "viral_reels": "every run pays Apify for its scan",
        "clean_images": "rewrites its uploads in place",
        "report_ask": "keeps no chat to answer, on purpose",
        "drive_upload": "its rows are the uploads: Upload again on the page",
        "clone_links_review": "/clone-links is retired",
        "login": "a sign-in, not work",
        "logout": "a sign-out, not work",
        "idle_logout": "an idle sign-out, not work",
        "viewing": "the start of View as, not work",
        "viewing_end": "the end of View as, not work",
    }
)


class NotReplayableError(ValueError):
    """The job cannot run again here; the message says why."""


class Replay(BaseModel):
    """What a job runs with when it runs again."""

    run: SkipValidation[Callable[..., Any]]
    kwargs: dict[str, Any]


class Replayed(BaseModel):
    job_id: str
    status: str
    error: str | None = None


def _paid(job: Job) -> Replay:
    provider = str(job.params.get("provider", ""))
    if provider not in FAKE_KEYS:
        msg = f"no fake for provider {provider!r}"
        raise NotReplayableError(msg)
    kwargs = {
        **job.params,
        "job_id": job.id,
        "api_key": FAKE_KEYS[provider],
        "assets_root": str(assets_root_for_actor(job.actor)),
    }
    return Replay(run=RESUMABLE[job.task], kwargs=kwargs)


def _trials(job: Job) -> Replay:
    run = trials.replay_run(job)
    label = str(job.params.get("label", ""))
    if run is None or label not in trials.RUNS:
        msg = "a /trials job from before rows kept what it ran with"
        raise NotReplayableError(msg)
    return Replay(run=trials.RUNS[label], kwargs=run.model_dump())


def replay_of(job: Job) -> Replay:
    """How `job` runs again. Raises `NotReplayableError`."""
    if job.task in NOT_REPLAYABLE:
        raise NotReplayableError(NOT_REPLAYABLE[job.task])
    if job.task in legacy_tools().entries:
        msg = "a per-model tool from before /generate: Recreate it on /generate"
        raise NotReplayableError(msg)
    if job.task in RESTARTS:
        restart = RESTARTS[job.task]
        return Replay(run=restart.run, kwargs=restart.kwargs(job))
    if job.task in RESUMABLE:
        return _paid(job)
    if job.task == trials.TASK:
        return _trials(job)
    msg = f"task {job.task!r} has no replay"
    raise NotReplayableError(msg)


def _faked_only() -> None:
    infra = settings.infra
    if infra.production or not infra.fake_externals:
        msg = "replays run only with OFM_FAKE_EXTERNALS=true, never in production"
        raise NotReplayableError(msg)


def replay(job_id: str) -> Replayed:
    """`job_id` run again here, against the fakes; its row tells how it went."""
    _faked_only()
    job = get_job(job_id)
    if job is None:
        msg = f"no job {job_id}"
        raise NotReplayableError(msg)
    found = replay_of(job)
    if not reopen_settled(job.id):
        msg = "it is running: wait for it to end"
        raise NotReplayableError(msg)
    run_job(job.id, found.run, found.kwargs)
    done = get_job(job.id)
    return Replayed(
        job_id=job.id,
        status=done.status if done else "gone",
        error=done.error if done else None,
    )


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run a stored job again on fakes.")
    parser.add_argument("job_id")
    args = parser.parse_args()
    configure_telemetry("ascend-replay")
    configure_logging()
    print(replay(args.job_id).model_dump_json())


if __name__ == "__main__":
    _main()
