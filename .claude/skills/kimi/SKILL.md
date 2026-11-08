---
name: kimi
description: Hand a task to the owner's second agent, Kimi Code CLI (a different model, with its own WebSearch and FetchURL), from the shell. Use it as REVIEWER (an independent review of a diff: before every ship, after a risky change) and as RESEARCHER (web research on a library, an API, a release, an error; a second opinion on a root cause or plan), also in parallel with your own work. The owner asked for this (2026-11-08).
---

# Kimi: reviewer and researcher

Kimi Code CLI 2.5.2 (`~/.kimi-code/bin/kimi.exe`). In this repo it reads
`AGENTS.md` (which sends it to `CLAUDE.md` and the runbooks) and `.mcp.json`
(Logfire EU), plus `~/.kimi-code/mcp.json` (serena, memory, codegraph). It has
WebSearch and FetchURL built in.

## When
- **Reviewer, every ship:** the diff about to ship (`ship` step 2b). Also
  after any risky change (migrations, auth, money, deploy scripts) before
  calling it done.
- **Researcher:** whenever an answer depends on the outside world: a
  library's current version/behaviour/support, an API's limits, release notes,
  an error message, prices. Also a second opinion on a root cause or a plan.
- Run it in the background and keep working; it takes ~40 s for a question,
  5-25 min for a review or a wide research task.
- Not for: commits, pushes, deploys, prod ssh, paid calls, edits. Those stay
  with you.

## How
1. Write the task to a file in the scratchpad: what to check, the exact scope
   (paths, `git diff -- <paths>`), the context it cannot guess. The mode adds
   the rules and the answer format.
2. Run (background for anything but a quick question):
   - review: `python .claude/skills/kimi/ask.py review <task.md> <name>`
   - research: `python .claude/skills/kimi/ask.py research <task.md> <name>`
   - quick read-only question: `python .claude/skills/kimi/ask.py ask <task.md> <name>`
   It prints only Kimi's final answer (review: `path:line | HIGH/MEDIUM/LOW |
   defect | evidence` lines or `NO FINDINGS`; research: answer, `Evidence:`
   with URLs and dates, `Unsure:`), then a `kimi -r <session>` command for a
   follow-up with the same context, and warns if the checkout changed during
   the run. Full stream: `$TEMP/kimi-<name>.jsonl`; answer: `$TEMP/kimi-<name>.md`.
   `KIMI_TIMEOUT` (seconds, default 1800) for longer tasks.
3. Treat its output like any reviewer's or source's: check each finding
   against the code (quote the line) and each research fact against its URL
   before acting. A finding you disprove: say why in the report. Tell the
   owner what Kimi found and what you did with it.
4. Kimi shares this checkout. Do not stage or edit the files under review
   while a review runs (`git diff` is what it reads).

## Gotchas
- An MCP server Kimi cannot start: `~/.kimi-code/logs/kimi-code.log`
  (`mcp server unavailable ... spawn <x> ENOENT`). npm `.cmd` shims do not
  spawn on Windows: use `node <the package's .js>` (codegraph, memory).
- Kimi has no Claude hooks and no Claude memory: what it must know goes in
  `AGENTS.md` / `CLAUDE.md`.
