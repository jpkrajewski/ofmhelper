# features/jobs

Running, recording, recovering and watching background jobs. The how: `rq` skill and `.claude/rules/jobs.md`.

## Who is told when the background stops (2026-11-20)
- The `sentry heartbeat` sweep runs `alive.report_alive` every 5 min: a queue with no live RQ worker (`cache/queue.queue_loads`), or Redis unreadable, logs `no worker takes jobs from: <queues>` (error: Logfire + Sentry) and sends the `sweeps` check-in as `error`; else `ok` (`ofmhelpers/sentry.heartbeat`).
- Sentry cron monitor `sweeps`: an issue after 2 failed or missed check-ins in a row (`failure_issue_threshold`, sent with every check-in), so the round while workers register after a deploy never alarms. A dead sweeps process = missed check-ins = the same issue.
- Alert "Background stopped: sweeps or workers" (https://krajewski-enterprise.sentry.io/monitors/alerts/1518865/, production only, made through the Sentry MCP): emails the issue owners, else every active member, at most every 30 min. Crashes, failed sweeps (`logger.exception`) and other errors: the older alert "Send a notification for high priority issues".
- A job a deploy cut and nothing could carry on logs `recovery: job <id> (<task>) was cut off by a restart` as error: its own Sentry issue.

## A deploy and running jobs: carried on, not drained (2026-11-20)
- The deploy stops the workers with a 120 s grace (`stop_grace_period`), then kills what still runs. On the new release `recovery.run_recovery_once` heals a paid job from its provider task id (`RESUMABLE`) or restarts a free local one (`RESTARTS`, `trials/folder.restart`: Generate, Regenerate, Upload).
- Why not blue-green or rolling: 26 migrations in 21 days over 20 deploys (nearly every deploy changes the schema) and prod /trials jobs up to 4.7 h (p95 3.8 h, Logfire 14 days). Old workers finishing beside new ones would run old code on the new schema for hours; a drain that long would hold every deploy and every queued job.
- Logfire 30 days before this: all 12 cut-off jobs were /trials; none failed after Generate (2026-11-09) and Upload (2026-11-18) restarted.
- Every task is classified, or `tests/unit/architecture/test_jobs_survive_a_deploy.py` fails: `RESUMABLE`, `recovery.RESTARTS`, `registry.DRAINED` (ends inside the 120 s grace; prod maxima 2026-11-20: viral_reels 86 s, links 30 s, drive_upload 6 s) or `registry.RECOVERED_BY_THEIR_SWEEP` (pinterest: the distribution round fails and retries it; clone_links_review: `recovery.resume_reviews`). A new task that may run past 120 s goes to the first two.
