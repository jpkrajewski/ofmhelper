# cache

Everything in Redis. Top-level because the worker and the kie.ai client need it too.

- `redis.py` — `get_redis()`: **the only Redis connection in the repo**, URL-keyed lazy global (rebuilt when `OFM_REDIS_URL` changes). `get_text`/`set_text`/`delete_text` are for optimisation caches only: they swallow `RedisError` as a miss. Anything that must not lose a write uses `get_redis()` and picks its own failure policy.
- `queue.py` — `enqueue(fn, ...)`: RQ handoff to the worker. Tests (`OFM_RQ_ASYNC=false`) run it inline.

Callers: every background-job router (`enqueue`); repository cache, rate limits, `web/ref_usage.py` (`get_redis`); kie.ai upload memo (`get_text`/`set_text`).
