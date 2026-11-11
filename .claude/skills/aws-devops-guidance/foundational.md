# AWS DevOps Guidance: the FOUNDATIONAL indicators, for this app

All 104 indicators marked `Category: FOUNDATIONAL` in the AWS Well-Architected DevOps Guidance (PDF read 2026-11-11), each as the rule it means here: one owner, AI agents, one VPS, Docker Compose, GitHub Actions. Full text of one: `cache/<ID>.md` (see `SKILL.md`). Owner rules in `CLAUDE.md` win.

## Development lifecycle (DL)
Local development
- `DL.LD.1` Every change is tried in the Docker dev stack (base + dev compose file), on prod data when it matters (`scripts/prod_to_dev.sh`). Never on prod, never on the host.
- `DL.LD.2` Dev is provisioned only by the compose files and images. A new tool goes in an image, a new first-run state is automated; no manual setup step.
- `DL.LD.4` Secrets are caught before commit: `gitleaks`, `detect-private-key`, `no-env-files` in `.pre-commit-config.yaml`. Never skip hooks.

Software component management
- `DL.SCM.1` Everything is in git on GitHub. `main` moves only through the promote PR.
- `DL.SCM.2` One working branch, `dev`, promoted to `main` at each ship. No feature branches, no worktrees.
- `DL.SCM.4` Code comes only from PyPI through `uv.lock` with exact pins, GitHub Actions pinned by commit SHA, binaries pinned in the image. Check advisories before adding a package.
- `DL.SCM.6` Docs live in the repo: `README.md` = server runbook, `CLAUDE.md` per module = rules and findings with sources. A change that alters how something is run updates them in the same batch.

Everything as code
- `DL.EAC.1` Services, limits, queues and deploy order are code: the compose files, `deploy.yml`, `scripts/deploy.sh`. Both deploy paths stay in step.
- `DL.EAC.3` Schema and data moves are Alembic revisions, run by the `migrate` service on every `up`. No standalone scripts.

Code review
- `DL.CR.1` Standards are enforced by tools, not by memory: ruff (`select = ["ALL"]`), mypy, djlint, biome, shellcheck, zizmor; conventions in `CLAUDE.md`.
- `DL.CR.2` Every ship diff gets an independent review by the second agent (`kimi` skill) before commit; the owner approves the release.
- `DL.CR.3` Done is defined: `CLAUDE.md` "Delivery gates" and "Done = full default suite green, with counts".
- `DL.CR.4` Linters take style and syntax, so review reads logic: money paths, retries, duplicate requests, data integrity, who sees whose data.

Continuous integration
- `DL.CI.1` Small batches into `dev`; one batch shipped before the next is started.
- `DL.CI.2` `ci.yml` runs on every push; nobody starts a build by hand.
- `DL.CI.3` Every build runs lint, types, tests in three groups, e2e (Playwright) and the Docker build. A new kind of check goes into CI and pre-commit, not into a habit.
- `DL.CI.4` Failures are reported the same way every time: `reports/latest/report.json`, counts, each failure with its error text, the 10 slowest tests.

Continuous delivery
- `DL.CD.1` Each ship deploys. Small, frequent releases over big ones.
- `DL.CD.2` Prod builds only the `origin/main` commit that passed CI. Never from a dev checkout, never with the dev overlay.
- `DL.CD.3` A deploy is checked after it lands: `/health` poll in `deploy.yml`, then Logfire errors for that release.
- `DL.CD.4` The whole deploy is the workflow. No hand step on the server; a new step goes into both deploy paths.
- `DL.CD.5` Deployable any time: `scripts/deploy.sh` is the escape hatch (it deploys a commit already on `origin/main`; it never commits or pushes). A deploy kills in-flight jobs, so anything long-running must be resumable or restartable (`features/jobs/recovery.py`: `RESUMABLE`, `RESTARTS`).

Advanced deployment strategies
- `DL.ADS.1` Pre-production is dev on a prod snapshot: migrations run on the snapshot (`tests/migrations/`), the failed prod job is replayed there.

## Quality assurance (QA)
- `QA.TEM.1` Tests run in the `tests` Docker service against a throwaway `ofmhelpers_test` database.
- `QA.TEM.2` Each test builds its own data; integrations run against fakes. Never the dev DB, the prod snapshot (except `-m snapshot`) or the production Drive folder.
- `QA.TEM.3` Results are kept: `reports/latest/`, CI run logs. Read the report, never a piped tail.
- `QA.FT.1` Unit tests for logic and math, fast, no network.
- `QA.FT.2` New behaviour gets an integration test first. A new outside service: interface + fake + contract test (fake always, real under `live`).
- `QA.FT.3` End-user check: e2e only where browser JavaScript is needed, then the owner tries it on dev.
- `QA.NT.1` Static checks on everything that is code: Python, templates, JS/CSS, shell, workflows.
- `QA.ST.1` Vulnerabilities are handled continuously: Renovate security PRs at once, GitHub security alerts, and every `ship` merges or fixes them (ship skill step 3).
- `QA.ST.3` Before designing anything that touches money, logins, secrets or other users' data: name what can go wrong (cost, leak, duplicate run, wrong owner) and how the design stops it.
- `QA.ST.4` Static security testing: ruff `S` rules, zizmor on workflows, gitleaks on commits.
- `QA.ST.6` Third-party components: exact pins, Renovate, advisory check before a new dependency.

## Automated governance (AG)
Secure access
- `AG.SAD.1` No long-lived shared passwords: SSH by key only, secrets only in the server `.env` and GitHub secrets, stored logins encrypted with the vault key.
- `AG.SAD.3` The pipeline is a production system: workflows linted by zizmor, actions pinned by SHA, deploy credentials only in GitHub secrets, no server address or key path in a committed file (public repo).
- `AG.SAD.4` People and agents do not change prod by hand. ssh is for diagnosis, after Logfire; changes go through a deploy.
- `AG.SAD.5` Break-glass exists and is written down: `scripts/deploy.sh`, the provider's console, `README.md` "Troubleshooting".

Data lifecycle
- `AG.DLM.1` A full restore needs four things: the Postgres dump, `.env` (above all `OFM_VAULT_KEY`), `uploads/`, `kieai_out/` (`README.md` "Backups"). A deploy never removes volumes.
- `AG.DLM.2` Encrypted in transit (HTTPS at nginx, HTTP redirects) and stored logins encrypted. No secret in logs, telemetry or job params.
- `AG.DLM.3` Data moves through the app's own machinery: Alembic revisions and queue jobs, repeatable and logged.
- `AG.DLM.4` Customer data is classified by where it may go: prod snapshot and `.env` are gitignored, nothing from them enters the repo, tests or an outside service.
- `AG.DLM.5` Every file the app writes has a deletion rule before it ships (the prod disk filled from generated media, 2026-11-11).
- `AG.DLM.6` One Postgres, reached only through repositories; one Redis connection. Each user sees only their own data.

Environments
- `AG.DEP.1` Two environments, dev and prod, told apart by the compose overlay and `deployment_environment`. The overlay is never auto-loaded, so it cannot apply on the server.
- `AG.DEP.2` Drift is removed on every deploy: `git reset --hard <sha>`, rebuild, `--force-recreate`. The server checkout is never edited.
- `AG.DEP.3` GitHub Actions is the one place deployments come from.

Compliance and guardrails
- `AG.ACG.2` A new outside service or package enters through the same door: `integrations/<name>/`, settings group, fake + contract test, advisory check, cost named before any paid call.
- `AG.ACG.3` Detective controls are tests and telemetry: architecture tests (`test_templates_use_components.py`, `test_palette.py`, `test_worker_services.py`), Logfire errors per release.
- `AG.ACG.4` Preventative guardrails are hooks and scripts that refuse: pre-commit (`alembic-single-head`, `no-env-files`, `push-tests`), `deploy.sh` refusing a dirty tree or a `HEAD` that is not `origin/main`, compose making the app wait for `migrate`.

Auditing
- `AG.CA.1` The audit trail is git history, GitHub Actions runs and Logfire (requests and jobs carry who and which release).
- `AG.CA.2` Configuration is inventoried in one place: `config/settings.py`, one group per module; the server `.env` is changed key by key, never wholesale.
- `AG.CA.3` Every exception to a rule carries its reason where it is made: a `noqa` with the why, a deliberate deviation in the module's `CLAUDE.md`.

## Observability (O)
- `O.SI.1` Telemetry answers business questions first: what a job cost, how long it took, whose it was.
- `O.SI.2` One tool: Logfire, one project, dev and prod split by `deployment_environment`.
- `O.SI.3` Everything is instrumented: a new job, route or outside call shows up as a span; logging through `get_logger`; no silent failure.
- `O.DIP.1` Web and worker logs, SQL and outbound calls land in the same `records` table, joined by `trace_id`.
- `O.DIP.2` Sign-in activity is in the same store, so a security question is one query.
- `O.CM.2` The big-incident plan is the runbook: `README.md` "Moving to a new server", "Backups", "Troubleshooting".
- `O.CM.3` After an incident the cause, the evidence and the fix are written down, dated, where the next person will read them (module `CLAUDE.md`, `README.md`).

## Organization (OA), the part that applies
- `OA.LS.2` Work is ranked by money and time for the business, not by technical interest.
- `OA.STD.6` You build it, you run it: whoever ships a change watches its deploy, `/health` and Logfire.
- `OA.TI.3` Feedback comes from the owner testing on dev; it is acted on before release.
- `OA.TI.4` Errors are tracked to a root cause with the quoted log line; the same error twice is a bug to fix, not to retry.
- `OA.TI.5` One approval gate, the owner's word (ship / commit / push / deploy). Everything before and after it is automated.
- `OA.BCL.2` Repeated manual work becomes a script or skill (`ship`, `prod_to_dev.sh`, `ui_screens.py`).
- `OA.BCL.3` Root causes over workarounds; flaky = bug; leave the touched code simpler.
- `OA.BCL.4` Limit work in progress: finish and ship a batch before starting the next; wait for another session's deploy.
- `OA.BCL.5` Blocked: name the exact blocker to the owner. Destructive or paid choices go to the owner.
- `OA.BCL.6` Act without asking inside the guardrails of `CLAUDE.md`.

## Deviations, on purpose
- `DL.LD.3` (commit early and often): commits happen only on the owner's word. Keep batches small instead.
- `DL.SCM.3` (artifact repository): no registry; the server builds the image from the deployed commit.
- `DL.ADS.3` (staggered deployments): one server, one copy of the app. The deploy drains workers (`stop_grace_period`) before the swap.
- `DL.CR.5`, `QA.ST.2`, `AG.SAD.2`, `AG.SAD.6`, `AG.ACG.1`, `O.CM.4`: need several teams, several scanners or a compliance programme; no counterpart here.

## Gaps (checked in the repo 2026-11-11; proposals, not tasks)
- `DL.ADS.2` No automatic rollback: a deploy that fails `/health` ends red and the app stays on the new, broken build. Going back by hand: the image of the release before (`ofmhelpers-app:<sha>`) is kept on the server; the Deploy workflow with that `sha`, or `scripts/deploy.sh <sha>`, runs it without a rebuild (README "Deploying"). A migration may drop or rename in the same revision, so a rollback can need a database restore.
- `AG.DLM.1` No recovery objectives, and the backup is a manual `pg_dump` (`README.md` "Backups"); nothing schedules it or copies it off the server.
- `O.CM.1` No automatic alert on prod errors or a failed health check found in workflows or scripts; errors are found by querying Logfire.
- `QA.ST.5` No dynamic security testing of the running app.
- `DL.SCM.5` No allowed-licence list for dependencies (public repo).
- `DL.EAC.2` Host network setup (nginx, ufw, sysctl, certbot) is done by hand from `README.md`, not from code.

## Not applicable (team structure, hiring, training, offices)
`OA.LS.1` `OA.LS.3` `OA.LS.4` `OA.STD.1` `OA.STD.2` `OA.STD.3` `OA.STD.4` `OA.STD.5` `OA.STD.7` `OA.TI.1` `OA.TI.2` `OA.BCL.1` `OA.BCL.7` `OA.BCL.8` `OA.AWE.1` `OA.AWE.2` `OA.PPD.1` `OA.PPD.2` `OA.PPD.3` `OA.PPD.4`
