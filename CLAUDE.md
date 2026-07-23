# CLAUDE.md

## Core Principles

- Prefer the simplest correct solution.
- Follow existing patterns before introducing new ones.
- YAGNI: don't add abstractions or dependencies unless needed.
- Make the smallest change that solves the problem.


## Validation

Before considering a task complete:

- Run `pre-commit run --files <modified-files>` when possible.
- If multiple files or project-wide changes were made, run `pre-commit run --all-files`.
- Run `uv run pytest` after code changes unless the task is documentation-only.
- If tests or pre-commit checks cannot be run, explain why.

## New code

Write small test after making changes and test them

## Self-Learning

- Suggest improvements to this file when recurring patterns are discovered.
- Never modify this file automatically.
