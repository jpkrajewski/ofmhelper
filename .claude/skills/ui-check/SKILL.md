---
name: ui-check
description: Run the edge-case UI check (scripts/ui_check.py) on a page against dev. Use after every JS, template or route change, before calling it done. It mocks a 500, an HTML answer, offline and a slow answer, clicks 50 times, feeds junk, and writes a report with screenshots.
---

# UI check

A screenshot is not proof. This check breaks the page on purpose and reports what the user would see.

## When
After a JS, template or route change, before "done". CSS-only changes: `scripts/ui_screens.py` is enough.

## Run
From Git Bash, dev stack up:
```
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test run --rm --no-deps -e OFM_UI_PASSWORD="$(grep ^APP_PASSWORD_ADMIN= .env | cut -d= -f2-)" tests uv run --frozen python scripts/ui_check.py <path> [--submit <selector> --post <glob>] [--real]
```
`<path>` is the page (`/spoof`), `--submit` the button, `--post` the URL glob the click posts to.

## What it checks
- Load: no console error, no `pageerror`; PNGs at 1440 and 390 wide.
- The POST is always mocked (nothing reaches the server or a paid provider): a 500, an HTML page, offline, a slow answer. Each must show plain words, bring the button back and throw no JS error.
- 50 clicks against a mocked 202: exactly one Idempotency-Key.
- Junk in every field: 10k characters, emoji, RTL, `<script>`, empty.
- `--real`: one real submit, waits for the card. Only on free pages (`/download-assets`, `/spoof`). Paid pages (generate, clone, edit) are mocked only, never `--real`.

## Output
`reports/ui-check/<page>-<stamp>/report.md` plus PNGs; exit 1 on any fail. Read the report and look at the PNGs, fix every fail, rerun, then give the owner the report path.

## Why not @playwright/cli
It would run on the host (CLAUDE.md: every check runs in Docker), its newest release is days old on a Playwright alpha, and it wraps the same engine already pinned in the tests container (as `tests/e2e/e2e_session.py` and `scripts/ui_screens.py` use it).
