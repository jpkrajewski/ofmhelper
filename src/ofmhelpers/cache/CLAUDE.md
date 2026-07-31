# Module purpose

Everything this app keeps in Redis. Two halves: the durable one (the RQ queue
the worker consumes) and the throwaway one (optimisation caches nothing else
depends on).

**`redis.py` holds the only Redis connection in the repo.** Every Redis-backed
feature — the RQ queue, `web/db/repositories/cached_repository.py`,
`web/middleware/ratelimit.py`'s counters, `web/ref_usage.py`'s picker ordering,
the kie.ai upload memo in `aigenproviders/kaiai/client.py` — calls
`get_redis()`. Never `Redis.from_url` anywhere else: one URL, one pool, one
seam a test points at a test broker.

Top-level rather than under `web/` because the worker process, the scraping
jobs and the kie.ai client all need it, and none of them are the web app.

# Module files

- `redis.py` — `get_redis()`, the URL-keyed lazy connection (built on first
  use, rebuilt if `OFM_REDIS_URL` changes, which is why it is one of the two
  sanctioned `global`s in the repo — see the root CLAUDE.md). Plus
  `get_text`/`set_text`/`delete_text`, the string key/value helpers for
  **optimisation caches only**: they swallow `RedisError` and report a miss, so
  a broker outage degrades to "slow", never "broken". Anything that must not
  silently lose a write uses `get_redis()` directly and picks its own failure
  policy.
- `queue.py` — `QUEUE_NAME`, `get_queue()`, `enqueue(fn, *args, **kwargs)`.
  The RQ handoff to the `worker` container, replacing FastAPI BackgroundTasks.
  In the test suite (`OFM_RQ_ASYNC=false`) `enqueue` calls the function inline,
  so TestClient sees results immediately and a task fn can still be
  monkeypatched with a lambda.

# Who calls this

`enqueue` — every router that starts a background job (see `web/CLAUDE.md`'s
job pattern) plus `scraping/instagram_stats_job.py`. `get_redis` — the
repository cache-aside layer, the rate-limit counters, `web/ref_usage.py`.
`get_text`/`set_text`/`delete_text` — `aigenproviders/kaiai/client.py`'s
upload memo, currently the only pure-optimisation cache.
