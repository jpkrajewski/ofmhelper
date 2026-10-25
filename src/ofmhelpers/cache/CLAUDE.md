# cache

Everything in Redis. Top-level because the worker and the kie.ai client need it too.

- `redis.py` — `get_redis()`: **the only Redis connection in the repo**, URL-keyed lazy global (rebuilt when `OFM_REDIS_URL` changes). `get_text`/`set_text`/`delete_text` are for optimisation caches only: they swallow `RedisError` as a miss. Anything that must not lose a write uses `get_redis()` and picks its own failure policy.
- `queue.py` — `enqueue(fn, ...)` / `enqueue_to(queue, fn, ...)`: RQ handoff to the worker. Tests (`OFM_RQ_ASYNC=false`) run it inline. Queues: `default` (compose `worker`, `OFM_RQ_WORKERS` 10), `clone-links` (`CLONE_LINKS_QUEUE`, /clone-links runs, compose `clone-links-worker`, `OFM_CLONE_LINKS_WORKERS` 5: a 120-link Generate never holds the default queue), `previews` (`preview-worker`, 1). Each is `python -m ofmhelpers.worker [queue]`. `JOB_QUEUES` = the queues `run_job` goes on. `active_job_ids()` (recovery's view of live jobs; None = unknown) reads, in the order a job moves, each job queue's list, then its `:intermediate` list (rq BLMOVEs a dequeued job there before the horse sets `current_job`), then every worker's `current_job` -- read in another order, a job just being dequeued was missed and failed as cut off (review #57).

Callers: every background-job router (`enqueue`); repository cache, rate limits, `web/ref_usage.py` (`get_redis`); kie.ai upload memo (`get_text`/`set_text`).
