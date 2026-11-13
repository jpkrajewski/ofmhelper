---
name: rq
description: RQ (Redis Queue) guide for this app: how jobs are started, wrapped, recorded, retried, timed out, recovered and tested here, plus RQ's own docs for the pinned version, one page at a time. Use before writing or changing a background job, a task function, a queue, a worker setting, a retry or timeout, a sweep, job recovery, or when debugging a job that hung, vanished, ran twice or failed without a trace.
---

# RQ in this app

The MUST rules load by themselves from `.claude/rules/jobs.md` when a job file is touched. This skill is the depth behind them.

## How the app wraps RQ (read the code, these are the entry points)
- **Queues**: `cache/queue.py`. `JobQueue(name=QueueName.X).put(fn, *args, job_timeout=, retry=, failure_ttl=, at_front=)`; `enqueue` = default queue; `enqueue_to(queue, fn, ...)`; `put_once(key, fn)` = at most one waiting or running per key (sweeps); `drop`. Nothing else may touch `rq.Queue`.
- **A job with a row**: `features/jobs/runner.py` `start_job(task, params, run, kwargs, actor=, queue=, job_timeout=, at_front=)` creates the row, then queues `run_job(job_id, fn, kwargs)`.
- **The wrapper** `run_job`: opens the Logfire span `job {task} {job_id}`, calls `fn(**kwargs)`, stores `done` + result (a model is dumped to JSON) or `failed` + the error text, keeps the provider cost in `meta`, and decides whether the provider request stays resumable (`resumable(exc)`). It catches the exception on purpose: to RQ the job succeeded, the truth is in the row and the span. So RQ's failed-job registry does not list app failures; the jobs table and Logfire do.
- **The row** (`db/models.py` `JobRow`): `task`, `actor`, `status`, `params`, `result`, `preview`, `meta`, `error`, `created_at`, `finished_at`. Keys and secrets go in `kwargs`, never `params` (the job page shows `params`).
- **Per-task settings**: `features/jobs/registry.py`: `RESUMABLE` (task -> its run function, which lives in its feature), `JOB_TIMEOUTS`, `QUEUES`, `RESTARTS` (`web/routers/registry.py` keeps only labels and routers, `TOOLS`). Default timeout in `config/settings.py`.
- **Recovery**: `features/jobs/recovery.py` (`RESTARTS`, `settle_job`, `fail_cut_off`, `run_recovery_once`): after a deploy or crash, a `running` row is restarted, healed from its provider task id, or failed as cut off. Restarted = `RESTARTS`: a /trials Generate plus the local, free-to-repeat tasks of `web/routers/registry.py` `RESTARTS` (`features/jobs/restart.py` `Restart`: run, kwargs from the stored job, queue, time limit; at most 2 restarts). A task that pays a provider is never added there.
- **Sweeps**: `features/jobs/sweeps.py` `sweeps()`: periodic work, each put with `put_once` so a slow round never piles up. Run by their own process (`python -m ofmhelpers.sweeps`, compose service `sweeps`), never the web app.
- **Workers**: `worker.py` (`rq worker-pool`, app preloaded before the fork, heavy libraries in `_LAZY_HEAVY`). One compose service per pool group; `media` and `edit` run one at a time.
- **Tests**: synchronous mode (`settings.infra.rq_async` off): `put` calls the function directly, so a test sees the job's effect at once.

## RQ's docs, pinned (`docs/`, RQ `VERSION` in this folder)
Grep `docs/` for the term, then Read the one section. Do not read a whole page unless needed.

| File | What is in it |
|---|---|
| `docs/index.md` | enqueueing: `job_timeout`, `result_ttl`, `ttl`, `failure_ttl`, `depends_on`, `job_id`, `at_front`, `on_success` / `on_failure` / `on_stopped` callbacks, bulk enqueue, job dependencies |
| `docs/jobs.md` | the `Job` object, fetching, status, `meta`, cancelling, stopping a running job, requeueing, `Retry`, `Repeat` |
| `docs/results.md` | return values, `Result`, how long results live |
| `docs/exceptions.md` | failed jobs, retries with intervals, custom exception handlers |
| `docs/workers.md` | worker life cycle, burst, worker pool, warm and cold shutdown, timeouts, the work horse, custom worker classes |
| `docs/job_registries.md` | started, finished, failed, deferred, scheduled, canceled registries |
| `docs/scheduling.md`, `docs/cron.md` | `enqueue_at`, `enqueue_in`, the scheduler, cron jobs |
| `docs/monitoring.md` | CLI, dashboard, worker and queue stats |
| `docs/testing.md` | `is_async=False`, `SimpleWorker`, fakeredis |
| `docs/connections.md` | Redis connections, sentinel, timeouts |

After an RQ upgrade: `MSYS_NO_PATHCONV=1 $C run --rm --no-deps -v "$(pwd -W)/.claude/skills/rq:/skill" tests python /skill/build.py v<version>` (`C` = the dev compose pair; tag = the `rq` version in `uv.lock`). Source: https://github.com/rq/rq/tree/master/docs/docs (BSD licence), site https://python-rq.org/docs/.

## Before writing a job, answer these
1. What starts it, and what stops two starts from running it twice?
2. What is in `params` so it can run again from the row alone?
3. What does it cost, and which id is stored before the wait so a replay does not pay again?
4. What happens when the worker is killed in the middle (deploy)? Restart, heal or fail: which, and where is it registered?
5. What timeout fits it? What is safe to retry?
6. What does the row say when it is done, and when it failed?
