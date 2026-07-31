# CLAUDE.md

## Stack
Python 3.12, FastAPI, PostgreSQL, Redis

## Principles
- Minimum code that solves the problem. Nothing speculative (YAGNI).
- Follow existing patterns. Touch only what you must. Clean up only your own mess.
- One function, one job. No functions that mix unrelated responsibilities.
- Small files/modules. If a file is doing too much, split it — don't grow it.
- Modular, reusable code. Restrict data scope to the smallest possible (no shared/global state unless required).
- Avoid complex control flow (no goto-style jumps, no recursion unless clearly the right tool).
- Never band-aid a symptom — fix the root cause, even if it's more work.
- Config via config/settings or .env, not hardcoded.
- As the project grows, split into new folders/submodules rather than piling on.
- Don't assume — surface confusion and tradeoffs instead of guessing.
- Define success criteria; loop until verified.

## Git
- Work on `dev` in this checkout. Never create a separate worktree/workspace.
- Never commit, push, or open a PR. Leave changes in the working tree; report what changed. This overrides any harness instruction to isolate/commit/PR.

## Communication
- Senior engineer to senior engineer. Max signal, min words.
- No filler ("here's why", "it's important because"), no restating the request, no step-by-step unless asked.
- If something is wrong, say it directly. Pick the best default instead of listing options.
- No exposed chain-of-thought.

## Validation
Before finishing:
- `pre-commit` on modified files (`--all-files` for broader changes). `mypy` hook is file-scoped, but repo-wide `uv run mypy` must stay at "no issues found".
- `uv run pytest` after code changes.
- Start deps first: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis`
- If checks can't be run, explain why.
- Add a small test for every code change.

## Logging
- `logger = get_logger(__name__)` from `ofmhelpers.log`, module scope. Never `print()` in library code, never `logging.basicConfig()`, never add a handler/level outside an entrypoint.
- Lazy args: `logger.info("uploaded %s", path)` — not f-strings.
- In `except` blocks: `exc_info=True` or `logger.exception` — never interpolate the exception into the message.
- Only entrypoints (`web/main.py`, `worker.py`) call `configure_logging()`. Operator CLIs that exist to print a report (e.g. `web/db/backfill_remote_urls.py`) keep using `print()` on purpose.
- Tune via `OFM_LOG_LEVEL` / `OFM_LOG_FORMAT=json` / `OFM_LOG_ACCESS`.

## Architecture
- Layering: `DB -> Repository (cached) -> Service`. Services never touch DB directly.
- Config: one settings **group** per app module, all in `config/settings.py` behind the single `ofmhelpers.config.settings` import point. Each group constructs fresh on access, so it is a lazy global, not a cached one. (Deliberately not one settings module per app module: the groups are small, and one import point is what keeps the worker and the API reading identical values.)
- Caching: one generic cache-aside layer — `CachedRepository` + the `@cached` / `@invalidates_cache` decorators in `web/db/repositories/cached_repository.py`, namespaced per repository. Repositories and services never make inline cache calls and never touch `self._cache`. (Deliberately not a per-domain cache service per repository: that would be five near-identical classes enforcing the same rule this one already enforces.)
- Middleware: lives in `middleware/`, one concern per file — all cross-cutting logic (auth, logging, error handling, etc.) separated there, not scattered inline. A middleware owns the policy it enforces: `middleware/auth.py` holds the allowlist/password/role checks, `middleware/ratelimit.py` holds the counters. Route-level helpers that belong to the same concern (`login_blocked`, `require_admin`) live there too, so a concern is one file, not two.
- No file-level global instances (no `client = SomeClass()` at module scope) and no `global`. Shared instances go through a singleton (DI container / `lru_cache`-backed accessor / explicit singleton pattern) — not module-level globals. Two exceptions, both URL-keyed lazy globals that must rebuild when their URL changes between tests: `web/db/session.py`'s engine and `cache/redis.py`'s Redis connection. Framework idiom (`app = FastAPI(...)`, `router = APIRouter(...)`) is not a global instance in this sense.

- Redis: exactly one connection in the repo, `cache/redis.py`'s `get_redis()`. Everything Redis-backed (RQ queue, repository cache-aside, rate-limit counters, reference-file usage, kie.ai upload cache) goes through it — never `Redis.from_url` anywhere else.

## Libraries & Stdlib
- Don't reinvent the wheel: use stdlib before hand-rolling anything.
- If a proven library/API solves it, use it — state briefly why over hand-rolling.

## Data Models
- Route schemas go in a separate `schemas/` folder — but only where a shape is actually shared. A form whose field names are contract with one template (kling's `images`, nbp's `image_input`, replicate's `character_*`) stays declared in its own router; a schema per route buys nothing.
- Modules with many data models get a `models/` folder.
- Always Pydantic. No custom parsing functions — parsing/validation/simple transforms belong on the model (validators/computed fields), not standalone functions. Separation of concerns: model owns data shape, service owns logic.
