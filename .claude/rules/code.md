---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "alembic/**/*.py"
---

# Code: where things go and how this code is written

Moved word for word from `CLAUDE.md` (2026-11-11); loads when a Python file is touched.

## Layout
One rule: `web/` is HTTP only. Where a new thing goes:
- `web/` app, middleware, `routers/<area>/`, templates, static. A route: params -> one call -> render.
- `features/<page>/` what a page or background job does (`service.py`, `jobs.py`, `models.py`). Math and static data in their own files, not next to the logic.
- `integrations/` everything that talks to an outside service: `ai_providers/<name>/`, `gdrive`, `instagram`, `llm`, `short_links`, `apify`, `stacked`, `fanvue`, `downloaders`, `signin`, `socials`, `senders/<name>/` (where events go out: Telegram, ...; `features/events` emits).
- `media/` local video and image work (ffmpeg, recorder, repurpose, exports).
- `db/` session, models, `repositories/`, `stores/`. `schemas/` shared Pydantic shapes. `config/`, `cache/`, `utils/`.

## Architecture
- `DB -> Repository (cached) -> Service`; services never touch the DB.
- Config: one settings group per module in `config/settings.py`, imported via `ofmhelpers.config.settings` (fresh per access).
- Background work goes through a FIFO queue, `cache.JobQueue(QueueName.X).put(fn, ...)` (`enqueue` = the default queue); how many run at once = its worker processes (`media`: one, for big local encodes); priority = who gets the CPU first, default on top, `media` at the bottom. New queue: a `QueueName` member + its entry in `InfraSettings.queues` (`config/settings.py`), its compose service, both deploys (`tests/unit/test_worker_services.py`).
- Caching only via `CachedRepository` + `@cached`/`@invalidates_cache`; no inline cache calls.
- Middleware in `middleware/`, one concern per file, owning its policy and route helpers.
- No module-level instances or `global`; shared instances via `lru_cache` accessor/singleton. Exceptions: `db/session.py` engine, `cache/redis.py` connection. `app`/`router` are fine.
- One Redis connection: `cache/redis.py` `get_redis()`.

## Python 3.15 in this code
- Heavy or typing-only imports: `lazy import` / `lazy from` at module level, never `if TYPE_CHECKING:`, no `from __future__ import annotations` (annotations are deferred natively). Ruff's `TC001`/`TC002` ask for it. A job-side heavy library also goes in `worker._LAZY_HEAVY`, so a forked job does not pay for it.
- A lazy name used at import time (base class, decorator, module-level call) loads right there: no gain. A lazy import failing fails at first use: keep "is it installed?" checks eager.
- Shared never-mutated dicts: `frozendict`. "No value given" markers: `sentinel("_NAME")`. File moves and copies: `Path.move` / `Path.copy`.

## Data Models
- Map all data to Pydantic (tool JSON, config, fixtures); no raw dicts or hand parsers. Large static data = package JSON + model.
- Validation/transforms on the model; logic in services.
- Shared route shapes in `schemas/`; one-template forms stay in their router. Many models -> `models/` folder.

## Logging
- `logger = get_logger(__name__)` (`ofmhelpers.log`), module scope; lazy `%s` args; `exc_info=True` in `except`.
- No `print()` in library code (report CLIs excepted); only `web/main.py`/`worker.py`/`sweeps.py` call `configure_logging()`.
- Tune: `OFM_LOG_LEVEL`, `OFM_LOG_FORMAT=json`, `OFM_LOG_ACCESS`.
