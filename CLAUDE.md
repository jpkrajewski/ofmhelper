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
- Config: one settings module per app module, not one global settings file.
- Caching: goes through a per-domain cache service (e.g. `UserCacheService`, owned by/paired with its repository) — never bare helper functions, never inline cache calls in a repository or service.
- Middleware: lives in `middleware/`, one concern per file — all cross-cutting logic (auth, logging, error handling, etc.) separated there, not scattered inline.
- No file-level global instances (no `client = SomeClass()` at module scope) and no `global`. Shared instances go through a singleton (DI container / `lru_cache`-backed accessor / explicit singleton pattern) — not module-level globals.

## Libraries & Stdlib
- Don't reinvent the wheel: use stdlib before hand-rolling anything.
- If a proven library/API solves it, use it — state briefly why over hand-rolling.

## Data Models
- Route schemas go in a separate `schemas/` folder.
- Modules with many data models get a `models/` folder.
- Always Pydantic. No custom parsing functions — parsing/validation/simple transforms belong on the model (validators/computed fields), not standalone functions. Separation of concerns: model owns data shape, service owns logic.
