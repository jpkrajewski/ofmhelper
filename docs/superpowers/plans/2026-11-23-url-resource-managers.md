# URL-aware Redis and SQLAlchemy Resource Managers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mutable Redis and SQLAlchemy globals with thread-safe URL-aware managers and remove their `PLW0603` Ruff suppressions.

**Architecture:** Each resource module owns a small manager containing a lock, current URL, and current resource. An `lru_cache(maxsize=1)` accessor owns the manager; existing public accessors delegate to it without changing caller-facing signatures.

**Tech Stack:** Python 3.15, Redis, SQLAlchemy, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-11-23-url-resource-managers-design.md`

## Global Constraints

- Preserve `get_redis() -> Redis`, `get_engine() -> Engine`, `get_sessionmaker()`, and `session_scope()` unchanged.
- Reuse a resource for an unchanged URL; close a replaced Redis client and dispose a replaced SQLAlchemy engine.
- Construct the replacement before changing manager state so a construction failure leaves the current resource available.
- Add no dependency, module-level resource instance, `global` statement, code comment, or docstring.
- Remove only the Redis/session `PLW0603` suppressions; do not sweep unrelated Ruff ignores.
- Run tests only through `scripts/test.sh`; keep verification scoped to affected tests.
- Do not commit, push, or deploy.

## Review Focus

- Concurrent first use or URL replacement must construct exactly one resource; pin with public-accessor thread tests in Tasks 1 and 2.
- A changed URL must clean up the old resource only after the replacement exists; pin cleanup and construction-failure tests in Tasks 1 and 2.
- An unchanged URL must return the identical resource without closing or disposing it; pin identity assertions in Tasks 1 and 2.
- Each new engine must receive both existing event listeners exactly once; pin listener counts in Task 2.
- Telemetry must still trace a newly created application engine without resetting removed private globals; run the existing real-query test in Task 2.

---

### Task 1: Redis resource manager

**Files:**
- Modify: `src/ofmhelpers/cache/redis.py`
- Create: `tests/unit/cache/test_redis.py`
- Verify: `tests/integration/test_cache_package.py`
- Verify: `tests/integration/web/test_repository.py`

**Interfaces:**
- Consumes: `settings.infra.redis_url -> str`, `Redis.from_url(url: str) -> Redis`, `Redis.close()`.
- Produces: private `_RedisManager.get(url: str) -> Redis`; private `_redis_manager() -> _RedisManager` cached with `lru_cache(maxsize=1)`; unchanged public `get_redis() -> Redis`.

- [ ] **Step 1: Write failing public-accessor tests**

In `tests/unit/cache/test_redis.py`, fake `Redis.from_url` with lightweight clients recording `close()` calls and set `OFM_REDIS_URL` per test. Add:
- `test_redis_is_reused_and_closed_when_its_url_changes`: two calls at one URL return one identical, open client; after a URL change, a different client is returned and the first client is closed exactly once.
- `test_redis_is_constructed_once_for_concurrent_calls`: eight `ThreadPoolExecutor` calls at one uncached URL return the same client and the fake constructor runs once; a short constructor sleep makes the old unlocked implementation fail.
- `test_redis_replacement_failure_keeps_the_current_client`: after a successful first call, a failing constructor at a new URL raises the injected `RedisError`, leaves the first client open, and returns that client when the original URL is restored.

- [ ] **Step 2: Run the Redis unit tests and verify failure**

Run: `scripts/test.sh tests/unit/cache/test_redis.py`
Expected: FAIL because the existing globals have no locking or close-on-replacement behavior. Read `reports/latest/summary.txt` and record counts and failure text.

- [ ] **Step 3: Implement `_RedisManager`**

In `src/ofmhelpers/cache/redis.py`, replace `_redis`, `_redis_url`, and the `global` logic with `_RedisManager`. Its `get(url)` holds a `threading.Lock`, returns the current client for an unchanged URL, otherwise constructs the new client, swaps URL/client state, closes the old client, and returns the new client. Add `_redis_manager()` with `lru_cache(maxsize=1)` and delegate `get_redis()` to `_redis_manager().get(settings.infra.redis_url)`.

- [ ] **Step 4: Run focused Redis behavior tests**

Run: `scripts/test.sh tests/unit/cache/test_redis.py tests/integration/test_cache_package.py tests/integration/web/test_repository.py`
Expected: PASS. Read `reports/latest/summary.txt`; report counts, any failures, and the ten slowest tests.

### Task 2: SQLAlchemy engine manager

**Files:**
- Modify: `src/ofmhelpers/db/session.py`
- Create: `tests/unit/db/test_session.py`
- Modify: `tests/integration/web/test_telemetry.py:166-175`
- Verify: `tests/integration/db/test_audit.py`

**Interfaces:**
- Consumes: `settings.infra.database_url -> str`, `sqlalchemy.create_engine`, `event.listen`, and existing `_new_transaction` and `_say_who_acts` listeners.
- Produces: private `_EngineManager.get(url: str) -> Engine`; private `_engine_manager() -> _EngineManager` cached with `lru_cache(maxsize=1)`; unchanged public `get_engine() -> Engine`.

- [ ] **Step 1: Write failing public-accessor tests**

In `tests/unit/db/test_session.py`, fake `sqlalchemy.create_engine` with lightweight engines recording `dispose()` calls, record `event.listen` calls, and set `OFM_DATABASE_URL` per test. Add:
- `test_engine_is_reused_and_disposed_when_its_url_changes`: unchanged URL returns the identical engine with no disposal; changed URL returns a replacement and disposes the first engine once.
- `test_engine_is_constructed_once_for_concurrent_calls`: eight concurrent `get_engine()` calls return one engine, construct once, and register `begin`/`before_cursor_execute` once each.
- `test_engine_replacement_failure_keeps_the_current_engine`: an injected construction failure leaves the current engine undisposed and available when the original URL is restored.

- [ ] **Step 2: Run the engine unit tests and verify failure**

Run: `scripts/test.sh tests/unit/db/test_session.py`
Expected: FAIL because the existing globals have no locking and the test double exposes unsynchronized construction. Read `reports/latest/summary.txt`.

- [ ] **Step 3: Implement `_EngineManager`**

In `src/ofmhelpers/db/session.py`, replace `_engine`, `_engine_url`, and the `global` logic with `_EngineManager`. Under a `threading.Lock`, reuse an unchanged-URL engine; otherwise create a new engine with the existing options, attach both listeners once, swap URL/engine state, dispose the old engine, and return the replacement. Add `_engine_manager()` with `lru_cache(maxsize=1)` and delegate `get_engine()` to it.

- [ ] **Step 4: Update telemetry setup without private globals**

In `test_the_apps_own_engine_traces_its_queries`, replace both `monkeypatch.setattr(session, "_engine", None)` calls with environment-based engine rebuilding: before the query, set `OFM_DATABASE_URL` to the current URL with a distinct `application_name` query parameter. Keep the real query, disposal, instrumentation cleanup, and span assertions unchanged.

- [ ] **Step 5: Run focused engine behavior tests**

Run: `scripts/test.sh tests/unit/db/test_session.py tests/integration/db/test_audit.py tests/integration/web/test_telemetry.py`
Expected: PASS, including a real traced `SELECT 42`. Read `reports/latest/summary.txt`; report counts, failures, and the ten slowest tests.

### Task 3: Remove suppressions and close the backlog item

**Files:**
- Modify: `pyproject.toml:212-215`
- Modify: `backlog.txt:1-265`
- Format: all Python files changed in Tasks 1 and 2

**Interfaces:**
- Consumes: working Redis and engine managers from Tasks 1 and 2.
- Produces: no `PLW0603` per-file ignore for either resource module; no singleton discussion left in `backlog.txt`.

- [ ] **Step 1: Remove the two Ruff ignores**

Delete the obsolete lazy-singleton comment and the `src/ofmhelpers/cache/redis.py` and `src/ofmhelpers/db/session.py` `PLW0603` entries. Do not change the adjacent `log.py` ignore or any other rule configuration.

- [ ] **Step 2: Format and lint the changed files**

Run the pinned Ruff formatter on the changed Python files, then Ruff lint on those files with the updated configuration.
Expected: no `PLW0603` finding and no formatting or lint failure. If Docker reports the known Windows bind-mount `EXE002` flood, verify the pinned host Ruff version and use the host Ruff result for the changed files as recorded in `BRAIN.md`.

- [ ] **Step 3: Re-run all focused tests together**

Run: `scripts/test.sh tests/unit/cache/test_redis.py tests/unit/db/test_session.py tests/integration/test_cache_package.py tests/integration/web/test_repository.py tests/integration/db/test_audit.py tests/integration/web/test_telemetry.py`
Expected: PASS with no collection errors. Read `reports/latest/summary.txt` and report counts, failures, and the ten slowest tests. Do not run the full suite before the owner’s ship request.

- [ ] **Step 4: Remove only the completed backlog entry**

Delete the opening lazy-singleton discussion and its alternatives from `backlog.txt`, ending immediately before `Why Viral page doenst have clone button like downloads??`. Leave that and every later entry unchanged.

- [ ] **Step 5: Review the final diff and stop after this item**

Inspect `git status --short` and the diff for the files owned by this plan. Confirm no unrelated file was modified, no stash was applied, and no commit was created. Report behavior proved, exact checks and counts, files changed, and the full-suite/manual-dev checks not run; then stop before the next backlog item.
