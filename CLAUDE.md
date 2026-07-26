# CLAUDE.md

## Stack
- Python 3.12, FastAPI, PostgreSQ, Redis

## Principles

- Minimum code that solves the problem. Nothing speculative.
- Follow existing patterns.
- Keep changes minimal (YAGNI).
- Don't assume. Don't hide confusion. Surface tradeoffs.
- Touch only what you must. Clean up only your own mess.
- Define success criteria. Loop until verified.

## Communication

- Talk like a senior engineer to another senior engineer.
- Maximum signal, minimum words.
- No educational content unless asked.
- No step-by-step explanations unless requested.
- No "here's why", "it's important because", or similar filler.
- Don't restate the request.
- Don't overanalyze obvious decisions.
- Make the smallest correct change and move on.
- If something is wrong, say it directly.
- If multiple solutions exist, pick the best default instead of listing every option.
- Don't expose chain-of-thought or internal reasoning.

## Validation

Before finishing:

- Run `pre-commit` on modified files (or `--all-files` for broader changes).
- Run `uv run pytest` after code changes.
- Start dependencies first:

  ```bash
  docker compose up -d postgres redis
  ```
- If checks can't be run, explain why.

## Logging

- `logger = get_logger(__name__)` from `ofmhelpers.log`, at module scope.
  Never `print()` in library code, never `logging.basicConfig()`, never add
  a handler or set a level outside an entrypoint.
- Lazy args: `logger.info("uploaded %s", path)`, not an f-string.
- In an `except` block use `exc_info=True` (or `logger.exception`) — don't
  interpolate the exception into the message and lose the traceback.
- Entrypoints (and only entrypoints) call `configure_logging()`:
  `web/main.py` and `worker.py`. Operator CLIs that exist to print a report
  (`web/db/backfill_remote_urls.py`) keep using `print()` on purpose.
- `OFM_LOG_LEVEL` / `OFM_LOG_FORMAT=json` / `OFM_LOG_ACCESS` tune it.

## New Code

- Add a small test for every code change.
