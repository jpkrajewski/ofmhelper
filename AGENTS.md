# AGENTS.md

Every coding agent (Codex, Cursor, Copilot, Claude, ...) follows the same rules
as Claude Code here. They are not repeated in this file:

1. **`CLAUDE.md`** (this folder): the project rules. Read it first, all of it.
2. **`CLAUDE.md` in each folder you touch**, from `src/ofmhelpers/` down to the
   file's own folder (e.g. `src/ofmhelpers/web/CLAUDE.md`, then
   `src/ofmhelpers/web/routers/CLAUDE.md`, then
   `src/ofmhelpers/web/routers/generation/CLAUDE.md`). Each one holds the rules
   and findings for that folder only.
3. **`CLAUDE.local.md`**, if present: local machine and server notes
   (gitignored).
4. **Runbooks** in `.claude/skills/<name>/SKILL.md`, plain Markdown, followed
   step by step:
   - `ship`: commit, push, deploy and watch it go live. Only when the owner
     says "ship", "commit", "push" or "deploy".
   - `logfire`: debugging with telemetry, before ssh/psql/logs.

The rules that matter most, in short:
- Never commit, push, merge or deploy on your own; on the owner's word, run
  `ship`.
- Several agents work in this one checkout at once (Claude sessions, Kimi,
  ...). Before an edit batch: `git status --short` + `git diff --stat`. Stage
  only the files you changed (`git add <paths>`, never `-A`), never
  `git stash`, never reset or check out a file you did not change. A file
  another agent is changing: leave it, say so.
- Every check runs in Docker: `scripts/test.sh [preset]`, never pytest on the
  host. From Git Bash, a `/container/path` argument to `docker compose run`
  needs `MSYS_NO_PATHCONV=1`, or Git Bash rewrites it into `C:/...` and the
  output lands in the checkout.
- Dependency updates come as Renovate PRs into `dev` (label `deps`); `ship`
  says what to do with them and with open Dependabot alerts.

## Kimi Code
- Reads this file, not `CLAUDE.md`: open the `CLAUDE.md` files above yourself.
- MCP: the repo's `.mcp.json` (Logfire, EU). First use: `/mcp` -> `logfire`
  -> log in.
- The runbooks above are not in Kimi's skill folders (`.agents/skills`,
  `.kimi-code/skills`): read `.claude/skills/<name>/SKILL.md` and follow it.
- No Claude hooks here: run `ruff format` / `ruff check --fix` on what you
  changed (pre-commit refuses unformatted files anyway).
