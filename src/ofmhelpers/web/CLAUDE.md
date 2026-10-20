# web

FastAPI app ("Ascend Global LLC — Content Ops"): one password per role (admin/VA), AI generation, media download/cleaning, landing-page CRM, file manager. Jinja2 pages + a small vanilla-JS polling layer.

**New background-job tool?** Reuse `stores/jobs.py` + `routers/task_helpers/` + one router file; no new plumbing.

## Layout
`main.py` (assembly only), `api_keys.py`, `middleware/`, `recovery.py`, `reel_watch.py`, `account_tracker.py`, `ref_usage.py`, `schemas/`, `templates_config.py`, `stores/`, `db/`, `routers/`, `templates/`, `static/`.
Dependency direction: **routers -> stores -> db**. Routers never open sessions; stores never render or raise `HTTPException`.

## Top-level files
- `main.py` — calls `configure_logging()`; middleware, static, lifespan (job history, recovery + reel + account sweepers), loop over `routers.ROUTERS`. Last `add_middleware` runs first: Session -> WriteRateLimit -> Auth.
- `middleware/auth.py` — all auth on `AuthMiddleware`: `check_password` (`APP_PASSWORD_ADMIN`/`_VA`), session gate, `require_admin`, `visible_actor` (VA sees only its own jobs; others 404). Allowlist `settings.web.public_paths`/`public_prefixes`. Unauthenticated: page -> 303 `/login?next=`, fetch -> `401 {"login_url"}`.
- `middleware/ratelimit.py` — counts failed logins only; `WriteRateLimitMiddleware` per-IP cap on writes. Redis errors fail open. Client = `request.client.host` (proxy needs `--proxy-headers`).
- `api_keys.py` — form pre-fills per provider/role (`Provider.default_key`).
- `ref_usage.py` — last-picked reference files in a Redis sorted set; errors swallowed.
- `reel_watch.py` — sweeper: claims profiles older than `rescan_interval_s` (12 h), one sequential worker job, claims expire (`rescan_claim_ttl_s`). Per profile up to 1 + `proxy_retries` proxies; transport-failing proxies leave the pool. **A profile with a good scan is never removed** ("not found" also hits flagged proxy IPs — 36 real profiles lost 2026-10-17). A just-added profile is dropped only if private, or not found everywhere *and* the logged-in check agrees.
- `account_tracker.py` — `/accounts` sweeper (`ofmhelpers.socials`, any platform): claims accounts older than `OFM_ACCOUNTS_RESCAN_INTERVAL_S` (6 h), queues per-platform jobs of `batch_size` with staggered `ProxyPool` start; per account up to 1 + `proxy_retries` proxies. Rate-limited on every try / direct connection down -> batch stops, claims expire (`claim_ttl_s`). Never-reached tries record nothing; answered failures record `error`, previous numbers stay. Accounts are never auto-removed.
- `recovery.py` — every 300 s, settles remembered provider requests: running -> wait; done/deleted/too old -> forget; failed poll -> one `status` call; done -> re-run via `registry.RESUMABLE` (resumes, never pays twice).
- `schemas/` — `persistence.py` (`Job`), `generation.py` (`ProviderChoice`, `ModelChoice`), `gallery.py` (`JobCard`, `TaskCard`, `CloneCard`/`CloneBatch`).
- `templates_config.py` — `get_templates()`; `static_url()` adds `?v=<hash>` to every CSS/JS link (nginx caches `/static` a week).

## stores/
- `jobs.py` — `create_job` -> `enqueue(run_job, ...)` -> `get_job`. `run_job` stores only the error message. Results live in the job's JSONB `result`. Pages use `list_jobs_page(tasks, offset, limit, actor)`: SQL filter/slice, heals only the page.
- `applications.py` — landing-page leads; write-once.
- `tracked_accounts.py` — /accounts: `list_accounts` (growth baselines per `growth_windows_h`), `get_account`, `get_history`, creators, notes, `record_scan` (one transaction: account row lock, upsert `tracked_posts` within retention, widen `covered_since`, snapshot, age-prune; see socials/CLAUDE.md). View windows are never stored — `TrackedAccount.window(days)` derives any period on read from the kept posts' views/dates (the list loads only those two columns).

## db/
`models.py`, `session.py`, `repositories/` (only DB code; inherit `cached_repository.py`). Alembic in `alembic/`; `RETIRED_TABLES` keeps unmapped old tables from being dropped.

## routers/
`routers/__init__.py` `ROUTERS` = the registration list: new page = one import + one entry.
- `task_helpers/` — shared upload -> job -> poll -> download: `ASSETS_ROOT` (sha256-deduped store; `assets_root_for(request)` — admin owns root, VA only `va/`; workers get `assets_root` kwarg), `build_ordered_paths` (manifest of new+reused files; the only `record_use` caller), `job_assets`/`serve_job_file` (both result shapes), `add_job_routes` (status page, poll, file). Upload safety: `safe_filename`, `require_upload_kind`, `media_response`.
- Tool shape: `POST /<prefix>/run` -> `{"job_id"}`; `GET /jobs/{id}`, `/jobs/{id}/status`, `/files/{id}/{index}`. New model = catalog entry, new provider = `aigenproviders/` subpackage.

### generation/
- Both pages start on `settings.aigen.default_provider` (`OFM_DEFAULT_PROVIDER`, default `higgsfield`).
- `index.py` — `/generate`: provider -> model -> fields (`ai-form.js` loads `GET /ai/fields`), cross-provider gallery with Recreate, paged via `gallery-scroll.js` (no sentinel = end). Tasks: `registry.GENERATE_TOOLS`.
- `ai.py` — `/ai`: fields rendered from the spec; `/ai/run` parses + `spec.check`s before upload/payment (400 names the field). Key goes to the worker only. `run_ai`: upload refs, `runner.generate`, register results.
- `legacy.py` — job routes of old per-model routers, no `/run`.
- `clone.py` — `/clone`: 1..`clone_max_images` subject images + N videos -> one Wan 3.0 job per video. `CLONE_TARGETS` per provider (new provider = one entry). Params provider-neutral. Duration = video length clamped to the schema. All videos probed and runs checked before queuing; `clone_max_videos` caps a batch. Batch rendered in place by `clone.js`.
  - Export: one dropdown from `ofmhelpers.exports` (Original, CapCut — default `OFM_EXPORT_DEFAULT`, one entry per `iphones/` profile) + Mirror switch (`ExportOptions`, start state `OFM_EXPORT_MIRROR`), stored as job `mirror`. The exported copy becomes the result; a failed export keeps the generated video. Old jobs' `capcut` bool maps to `capcut`/`original`.
  - Cost panel (`clone-cost.js`): length x credits/s x $/credit per provider (`clone_pricing()`); both bill ~2x source length. kie.ai `clone_credits_per_video_s` x `kie_usd_per_credit`; Higgsfield `clone_hf_credits_per_video_s` x `hf_usd_per_credit` (60% discount, measured 2026-10-18). `CloneTarget.max_reference_s` (Wan 3.0: 15 s, every provider): longer videos flagged in the panel and rejected at submit.
- Fake provider (`aigenproviders/fake/`) runs the real pipeline for free (e2e).

### downloads/
- `index.py` picker; `videos.py` (`/download-videos`), `images.py` (grouped-by-URL results); `clean_image.py`.
- `reels.py` — `/reels`: watched Instagram profiles in groups, ranked by `schemas.Leaderboard`; one flat feed filtered by `?group=`/`?rank=` (`VIEW_TIERS`: 50k/100k/200k/500k/1M+), paged 60. Scans keep reels within `max_age_days`, >= `min_views`, <= `max_duration_s` (15 s, Wan 3.0's cap; the feed hides longer stored reels too). `POST /reels/watch` adds/moves, queues scans (never in-request). Picks in `sessionStorage`; `POST /reels/clone` saves picks to the store and redirects to `/clone?video=`; `POST /reels/download` returns mp4/zip.

### tracking/
- `accounts.py` — `/accounts`: KPI cards (total followers, period views, unreachable count) + table (account w/ blue verified badge, followers, growth, views vs previous period, creator tag, notes), default sort followers; header click sorts, clicking the current one flips `order`. Controls: instant JS search over rendered rows (`data-search`), period select (`?period=`), creator select. No status/platform filters or badges in the UI (backend still accepts `platform`/`status`); private/failed-sync rows are red (`data-problem`, reason as tooltip). Creator is assigned from the selection card above the table (shown only while rows are picked) or the Add dialog, never inline. All state in `schemas.AccountQuery` (blank params = unset; `link()`/`sort_link()`). JSON posts `/accounts/add`, `/creator`, `/delete` (bulk ids), `/{id}/notes`. `/accounts/{id}`: identity + period select, 4 KPIs, daily follower line, every post of the period by views (thumbnail, "N h/d ago", `<progress>` bar). Banner when accounts were synced without a working Instagram login (`metrics.limited`). Metric rules: 0 = confirmed zero, dash = no data, comparison blank when unknown; growth % only with a non-zero baseline. Phone: rows become cards. `accounts.js` serves both pages.

### Others
- `image_tools/character_sheet.py` — admin; 2..N images side by side into one PNG; uploads saved `NN_<name>`.
- `video_tools/uniqify.py` — N variants zipped (stored); zip is the only result.
- `admin/` (router-level `require_admin`): `file_manager.py` (`_safe_path`), `action_log.py` (`TASK_STATUS_PREFIX` — add new task types), `applications.py`, `cookies.py`.
- `auth.py` (`/login`, `/logout`); `apply.py` (`POST /apply`, only public write, PRG); `refs.py` (reuse browser: 5 last-picked + 5 last-uploaded; `?limit=N` up to 60 = newest uploads; paths outside the session's store 403).

## templates/ and static/
- Pages extend `base.html`: header bar = `nav_items` (Generate, Clone, Viral Reels, Download Assets, Accounts), the rest under "More" = `more_items` (Uniqify; admins also Character Sheet, Applications, File Manager, Action log, Instagram cookies). `[hidden]` always hides (global rule) -- toggle `hidden`, not `style.display`. Partials: `_file_picker.html`, `_provider_fields.html`, `_ai_fields.html`, `_asset_grid.html`/`_asset_media.html` (no filename of its own), `job_status.html`, `_generate_gallery_card.html` (JS twin: `buildResultCard`).
- `static/css/app.css` is the whole design system; use its components, no `<style>` blocks. JS-queried class names are contract. Mobile-first.
- JS: `generation.js` (submit -> poll -> render for `form[data-prefix]`), `file-picker.js`, `ai-form.js`, `session.js` (loaded first; global 401 handling + wall-clock idle logout), `gallery-scroll.js`, `live-gallery.js` (cross-session sync, paused when hidden), `prompt-highlight.js`, `nav.js`.
