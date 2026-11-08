---
name: ship
description: Commit, push and deploy this repo to production, then watch it go live. Use ONLY when the owner says "ship", "commit", "push" or "deploy" in this turn — never on your own initiative. Runs the deploy test gate, commits on dev, pushes, watches CI, merges the promote PR (that merge is the production deploy), watches the Deploy workflow, checks /health and Logfire errors for the new release, and reports.
---

# Ship

The owner's word ("ship" / "commit" / "push" / "deploy") is the only trigger.
Without it: report what changed and stop. Approval covers this one batch only.

Each step must pass before the next. A failure: stop, quote the error, fix it
or report it. Never `--no-verify`, never force-push, never `git stash`.

## 1. Own files only
Other Claude sessions share this checkout.
- `git status --short` and `git diff --stat`: name every file. Stage only the
  files this session changed (`git add <paths>`, never `git add -A`). A file
  another session is mid-way through: leave it, say so.
- `git fetch origin && git status -sb`: dev behind origin/dev means another
  session pushed. `git pull --ff-only` first; never rebase someone else's work.

## 2. Gate
`scripts/test.sh deploy` (all + heavy + live; free, no `paid`). Report
pass/fail/skip counts, each failure with its error text, the 10 slowest
(from `reports/latest/report.json`). Red = stop.

## 2b. Independent review by Kimi
While the gate runs: the `kimi` skill, a read-only review of the diff that is
about to ship (code and config; not uv.lock, not moved docs). A HIGH finding
is fixed, or disproved with the quoted code, before the commit; anything fixed
means step 2 again. Name every finding and what happened to it in the report.

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

## 9. Report
Commit sha, what shipped (one line per feature/fix), test counts, CI and
deploy run links, /health, Logfire errors since release (or "none"), anything
not run.
