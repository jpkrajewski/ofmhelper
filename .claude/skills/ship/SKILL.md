---
name: ship
description: Commit, push and deploy this repo to production, then watch it go live. Use ONLY when the owner says "ship", "commit", "push" or "deploy" in this turn — never on your own initiative. Runs the deploy test gate, commits on dev, pushes, watches CI, merges the promote PR (that merge is the production deploy), watches the Deploy workflow, checks /health, Logfire errors and new Sentry issues for the new release, and reports.
---

# Ship

The owner's word ("ship" / "commit" / "push" / "deploy") is the only trigger.
Without it: report what changed and stop. Approval covers this one batch only.

Each step must pass before the next. A failure: stop, quote the error, fix it
or report it. Never `--no-verify`, never force-push, never `git stash`.

## 0. Open the ship
`mkdir -p reports/hooks && git rev-parse --short HEAD > reports/hooks/ship.open`.
The git and tests hooks (`.claude/hooks/guards/`) let commit, push, the
promote merge and the deploy gate through while this file exists, also in the
next session when a ship was cut short. Step 10 removes it.

## 1. Own files only
Other Claude sessions share this checkout.
- `git status --short` and `git diff --stat`: name every file. Stage only the
  files this session changed (`git add <paths>`, never `git add -A`). A file
  another session is mid-way through: leave it, say so.
- `git fetch origin && git status -sb`: dev behind origin/dev means another
  session pushed. `git pull --ff-only` first; never rebase someone else's work.

## 2. Gate
`scripts/test.sh deploy` (all + heavy + live; free, no `paid`). Report what
`reports/latest/summary.txt` says (`scripts/test_summary.py`: counts, files
that did not import, each failure with its error, the 10 slowest). Green =
`test.sh` exit 0 AND `RESULT GREEN`; anything else = stop. Never read
report.json with a reader of your own: its per-test list leaves out files
that failed to import (2026-11-20: "6285 passed" reported green over 5 errors).

## 2b. Independent review by Kimi
While the gate runs: the `kimi` skill, a read-only review of the diff that is
about to ship (code and config; not uv.lock, not moved docs). A HIGH finding
is fixed, or disproved with the quoted code, before the commit; anything fixed
means step 2 again. Name every finding and what happened to it in the report.
The review is also against the rules: give Kimi the `.claude/rules/*.md` files
whose `paths` match the changed files and have it name each broken rule (for a
Zalando rule, its id). A `tests/unit/architecture/baseline.json` that grew is
a HIGH finding.

## 3. Dependencies (every ship, before the commit)
Renovate opens the bump PRs (into `dev`, label `deps`; `renovate.json`);
GitHub's Dependabot only raises alerts. Both are checked here, so a ship also
keeps the dependencies current.
- `gh pr list --base dev --label deps --json number,title,labels,statusCheckRollup`
  - green, not a major update: merge it (`gh pr merge <n> --merge`), then
    `git pull --ff-only`.
  - a major update (title says "major", or its own PR): do not merge; name it in
    the report, the owner decides.
  - red: open the failing check (`gh pr checks <n>`, `gh run view <id>
    --log-failed`). A small fix (an API rename, a test pinned to an old value):
    fix it on dev in this batch and close the PR as superseded. Otherwise leave
    it open and name it with the error line.
  - Never merge a Renovate PR whose base is `main` ("Configure Renovate"
    targets main): merging main is the production deploy.
- `gh api "repos/jpkrajewski/ofmhelper/dependabot/alerts?state=open" --jq
  '.[] | "\(.security_advisory.severity) \(.dependency.package.name)
  fix:\(.security_vulnerability.first_patched_version.identifier)"'`
  - an alert with a fix and no open Renovate PR for it: `$C --profile test run
    --rm --no-deps -T tests uv lock --upgrade-package <name>` (a security fix
    skips the two-week age rule), rebuild the test image
    (`$C --profile test build tests`).
  - an alert with no fix: name it in the report.
- Anything merged or upgraded here: step 2 again before the commit.

## 4. Commit and push dev
Conventional message (`feat:` / `fix:` / `test:` / `chore:`), one line saying
what the owner gets, as earlier commits do (`git log --oneline -10`). Then
`git push origin dev`. A push touching `.github/workflows/` needs the gh token's
`workflow` scope; if refused, the owner runs `! gh auth refresh -s workflow`.

## 5. CI on dev
`gh run list --workflow CI --branch dev --limit 1`, then `gh run watch <id>
--exit-status`. Red: `gh run view <id> --log-failed`, fix, back to step 2.

## 6. Promote = deploy
Green CI opens or updates "Promote dev to main"
(`gh pr list --base main --head dev`). Merge it with a merge commit:
`gh pr merge <n> --merge`. This is the production deploy.

Before the merge, look at what runs on prod: the deploy kills every running
job (120 s grace). `running_jobs()` in the prod app container (read-only):
a job a person waits on (trials Generate/Upload, threads, edit, clone) =
wait for it to end, or tell the owner which and how long, and merge on
their word. Prod 2026-11-18: a deploy cut a trials Upload of 6 at 23 min,
the 6 never reached Drive and lost their selection (owner: "shit is
missing").

## 7. Watch the deploy
CI on main (it reuses the dev run when the tree is identical), then the Deploy
workflow: `gh run watch <id> --exit-status` for each. Deploy red: its
"Report container state on failure" step has `compose ps` + the last 100 log
lines — quote them.

## 8. Live check
- `curl -s -o /dev/null -w '%{http_code}' https://ascendglobal.cash/health` = 200.
- Logfire (skill `logfire`), the release is `git rev-parse --short HEAD` of main:
  errors on production since the deploy started, excluding retried outbound
  HTTP spans. New exception types since the release = report them.
- Sentry (MCP server `sentry`, project `ofmhelpers`): `search_issues` with
  `environment:production firstSeen:-1h` and that release. Every issue first
  seen since the deploy = report it with its link. First prove production
  reaches Sentry at all: `find_monitors` (project `ofmhelpers`, environment
  `production`): the `sweeps` monitor's last check-in is after the deploy
  (the sweeps process checks in every 5 minutes, `ofmhelpers/sentry.py`
  `heartbeat`; wait one interval if needed). No fresh check-in = production
  sends nothing: report that, never "no issues". The MCP not signed in: say so.

## 9. Report
Commit sha, what shipped (one line per feature/fix), test counts, CI and
deploy run links, /health, Logfire errors since release (or "none"), Sentry
issues new since release (or "none", or why it could not be read), anything
not run.

## 10. Reflect
After the report (CLAUDE.md "SELF-LEARNING"): update `BRAIN.md` (critical only,
prune stale lines, max 40) and move this session's unlogged findings to
`IMPROVEMENTS.md`. Say in one line what was added or removed.
- Read the session's plan file (if any) against what shipped: every item
  dropped, reverted or done another way is a candidate lesson.
- A trap a test or hook can catch: write that check instead of a line (or
  delete the line it replaces).
- `BRAIN.md` lines keep `<added> / hit <date>:`; bump the hit date of every
  line that helped this session. `IMPROVEMENTS.md` lines keep their
  `[agent can do]` / `[owner decides]` / `[needs owner check]` tag.
- Close the ship: `rm -f reports/hooks/ship.open`.
