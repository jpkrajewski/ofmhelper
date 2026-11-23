# URL-aware Redis and SQLAlchemy resource managers

## Intent

Replace the hand-written mutable globals in `src/ofmhelpers/cache/redis.py` and `src/ofmhelpers/db/session.py` with small lock-protected managers. Preserve the existing public API while making resource replacement thread-safe and removing the two `PLW0603` per-file Ruff suppressions from `pyproject.toml`.

## Goals

- Return the same Redis client or SQLAlchemy engine while its configured URL is unchanged.
- Build a replacement when the configured URL changes.
- Close a replaced Redis client and dispose a replaced SQLAlchemy engine.
- Serialize resource inspection and replacement with a lock.
- Register the existing SQLAlchemy event listeners exactly once on each new engine.
- Create each manager through an `lru_cache` accessor, with no module-level resource instance or `global` statement.
- Keep `get_redis()`, `get_engine()`, `get_sessionmaker()`, and `session_scope()` signatures unchanged.

## Non-goals

- No caller rewrites or architecture changes outside these resource modules and their directly affected tests.
- No repository-wide Ruff suppression cleanup. This item removes only the two Redis/session `PLW0603` entries and adds no replacement suppression for the new implementation.
- No database schema, configuration, or dependency changes.

## Approaches

- Chosen: a small manager per resource, holding its lock, current URL, and current resource, with the manager itself returned by an `lru_cache` accessor. This preserves explicit cleanup and makes replacement atomic.
- Rejected: URL-keyed `functools.cache`/`lru_cache` directly around resource constructors. It is shorter but does not explicitly close or dispose evicted resources.
- Rejected: retaining the current globals with narrower suppressions. It preserves the race and continues hiding all global-state warnings in each file.

## Tests and verification

- Extend focused tests through the public accessors for same-URL identity, URL replacement, and old-resource cleanup.
- Update `tests/integration/web/test_telemetry.py` so it no longer resets the removed `_engine` global.
- Run the directly affected cache, repository-cache, session, audit, and telemetry tests through `scripts/test.sh`.
- Run Ruff on the changed Python files and `pyproject.toml` configuration with no `PLW0603` suppression for either resource module.
- Remove only the completed singleton item from `backlog.txt` after verification; leave every other backlog entry untouched.
