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
- `integrations/` everything that talks to an outside service: `ai_providers/<name>/`, `storage/<name>/` (one `StorageClient` interface; `gdrive` today), `instagram`, `llm`, `short_links`, `apify`, `stacked`, `fanvue`, `downloaders`, `signin`, `socials`, `senders/<name>/` (where events go out: Telegram, ...; `features/events` emits).
- `media/` local video and image work (ffmpeg, recorder, repurpose, exports).
- `db/` session, models, one package per table: `db/<table>/repository.py`, `store.py`, `schema.py`. `config/`, `cache/`. No `utils/`: a helper lives in the module that uses it (`web/time_zones.py`, `integrations/outbound.py` `ProxyPool`).
- Pydantic shapes: `schema.py` of the lowest module that makes them (`db/<table>/`, `integrations/<name>/`, `features/<page>/`). No shared schemas folder. A `schema.py` is a root module, like `constants.py`: it imports nothing from the project; the code that fills a shape imports the schema, never the other way (owner 2026-11-22; `media/repurpose/checker/schema.py`). Every `schema.py` follows it, no exceptions: `tests/unit/architecture` `test_constants_and_schemas_import_nothing_from_the_project`.

## Architecture
- `DB -> Repository (cached) -> Service`; services never touch the DB.
- Config: one settings group per module in `config/settings.py`, imported via `ofmhelpers.config.settings` (fresh per access).
- Background work goes through a FIFO queue, `cache.JobQueue(name=QueueName.X).put(fn, ...)` (`enqueue` = the default queue); how many run at once = its worker processes (`media`: one, for big local encodes); priority = who gets the CPU first, default on top, `media` at the bottom. New queue: a `QueueName` member + its entry in `InfraSettings.queues` (`config/settings.py`), its compose service, both deploys (`tests/unit/test_worker_services.py`).
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
- Writing a Pydantic class or calling the model API: the `pydantic` skill first (its own docs for the release in its `VERSION`; the `skills` guard asks for it). JSON in = `Model.model_validate_json(text)`, an object in = `model_validate(obj, from_attributes=True)`; a spec filled step by step is not `frozen`.
- `getattr`, `vars()` and `__dict__` are banned in the whole repo, `operator.attrgetter`, `__getattribute__` and `inspect.getattr_static` with them (owner 2026-11-22: "just ban vars and __dict__ everywhere"): read the typed field, iterate the model, validate an object with `from_attributes=True`, key a mapping by the name; a per-thread store is a `threading.local` subclass with typed attributes; a `cached_property` is replaced by plain assignment, dropped with `del`. Refused at edit time (`no_getattr` guard), at commit (`scripts/check_no_getattr.py`) and in CI (`tests/unit/architecture/test_no_getattr.py`, no baseline).
- No `*args` / `**kwargs` in a function: name every parameter and type it (owner 2026-11-22: "ban *args and **kwargs in functions"). Checked by `tests/unit/architecture/test_no_star_args.py`, no baseline.
- Validation/transforms on the model; logic in services.
- Route shapes in the `schema.py` of the module that makes them; one-template forms stay in their router. Many models -> `models/` folder.
- No UPPERCASE module constants (`_HASH_NAMES`, `_MIN_STEP`, `_SHAKE_BANDS`): every fixed value is a field with a default on a frozen Pydantic class in the folder's own `constants.py`, one class per area, read from an instance the code is given, or held as the `constants` field of the settings model that uses it. `constants.py` is a root module: it imports nothing from the project, and a base class its constants need lives in it too (owner 2026-11-22; the pattern: `media/repurpose/transformations/constants.py`, `VideoSettings.constants`). New and changed code follows it; existing modules move only in a refactor the owner asks for. A value someone may tune (a cap, tries, a timeout, concurrency) is not a constant: it is a field of the module's settings group in `config/settings.py` with its env name (owner 2026-11-22: the spoof try cap is `RepurposeSettings.max_tries`, not a constants class).

## Logging
- `logger = get_logger(__name__)` (`ofmhelpers.log`), module scope; lazy `%s` args; `exc_info=True` in `except`.
- Every `except` raises, or logs with its trace (`logger.exception`, `exc_info=True`, `span.record_exception`); `contextlib.suppress` is quiet too. A deliberate quiet one (gone already, a parser's None, a refusal shown on the page) says why on its `except` line: `# silent: <reason>`. A gone-meanwhile `FileNotFoundError` is `logger.debug(..., exc_info=True)`, any other `OSError` a warning. Checked by `tests/unit/architecture/` `test_failures_leave_a_trace` (baseline `silent_excepts`).
- No `print()` in library code (report CLIs excepted); only `web/main.py`/`worker.py`/`sweeps.py` call `configure_logging()`.
- Tune: `OFM_LOG_LEVEL`, `OFM_LOG_FORMAT=json`, `OFM_LOG_ACCESS`.
