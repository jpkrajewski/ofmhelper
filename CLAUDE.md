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

## Validation

Before finishing:

- Run `pre-commit` on modified files (or `--all-files` for broader changes).
- Run `uv run pytest` after code changes.
- Start dependencies first:

  ```bash
  docker compose up -d postgres redis
  ```
- If checks can't be run, explain why.

## New Code

- Add a small test for every code change.
