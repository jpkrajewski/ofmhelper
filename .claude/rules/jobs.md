---
paths:
  - "**/jobs.py"
  - "**/tasks.py"
  - "src/ofmhelpers/cache/queue.py"
  - "src/ofmhelpers/worker.py"
  - "src/ofmhelpers/features/jobs/**"
  - "src/ofmhelpers/db/stores/jobs.py"
---

# Background jobs (RQ): MUST

Depth: `rq` skill (RQ's docs for the pinned version, one page at a time, and how this app wraps it). Enforced by `tests/unit/architecture/` (baseline only shrinks).

## Starting work
- User-visible work starts with `start_job(...)` (`features/jobs/runner.py`): it writes the job row (`db/stores/jobs.py` `create_job`), then queues `run_job` (same file), the wrapper that records `done` + result or `failed` + error. No bare `enqueue(some_function)` for work a person waits for or pays for.
- Queues only through `cache.JobQueue(name=QueueName.X).put(...)` / `enqueue` / `enqueue_to`. Never `rq.Queue` directly, never `BackgroundTasks`, never a thread.
- The same work asked twice (double click, retry, sweep) runs once: `put_once(key, ...)` for sweeps, a check on the existing row for jobs.
- Arguments are small and JSON-safe: ids and plain values, `str(path)` not `Path`, never a model instance, an open file or a secret. The job loads what it needs by id.

## A job is replayable
- It can be run again from its stored `params` alone and ends in the same state. Every step first checks whether it is already done.
- A paid provider call stores the provider's task id BEFORE waiting on it, so a replay picks the task up instead of paying again.
- A task that can continue after a restart is registered for recovery (`features/jobs/registry.py`: `RESUMABLE` for a paid task that continues from its provider task id; `RESTARTS` for local work that is free and the same when run twice, never for a task that pays a provider). A deploy kills running jobs: decide what happens to this one.

## A job leaves a record
- `params`: everything needed to run it again. `result`: a Pydantic result model (stored as JSON), not a hand-built dict. `meta`: what happened on the way: provider task ids, cost, timings, draws. `error`: the real error text.
- Reading the row tells the story without Logfire; Logfire has the trace (`job {task} {job_id}` span) for the detail.
- A failure is never swallowed inside the task: let it raise, `run_job` records it.

## Limits
- Every job has a timeout that fits it (default from settings, `JOB_TIMEOUTS` in `features/jobs/registry.py`). A job that may run longer says so there.
- Retries are explicit: `retry=Retry(max=..., interval=[...])` and only for work that is safe to repeat.
- Heavy libraries a job needs are imported lazily and listed in `worker._LAZY_HEAVY`.
- A new queue: `QueueName` member + `InfraSettings.queues` entry + compose service + both deploys (`tests/unit/test_worker_services.py`).
- Worker code changes need `restart worker`; there is no reloader on purpose.

## Where the code is
- `features/jobs/runner.py`: `start_job`, `run_job`. `features/jobs/registry.py`: `RESUMABLE`, `JOB_TIMEOUTS`, `QUEUES`, `RESTARTS`. A task's run function lives in its feature (`features/<page>/`), never in a router.
- Jobs waiting in Redis name functions by the dotted path they were queued under. A run function or `run_job` that moves keeps its old path importable for a release: a plain re-import in the router it left (`web/routers/...`: router -> feature is a legal import), and for `run_job` the hand-over in `db/stores/jobs.py` (`register_runner`; the store never imports the runner). `tests/unit/web/test_import_surface.py` `QUEUED_PATHS` lists them.
