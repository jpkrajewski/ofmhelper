# Module purpose

The FastAPI web app ("Global Ascend LLC — Content Ops"): a single-password,
two-role (admin/VA) internal tool for AI content generation (every model of
every provider in `aigenproviders/`: kie.ai, Higgsfield), media
downloading/cleaning, the landing-page application CRM, and
file management. No
SPA framework — Jinja2 server-rendered templates + a small shared vanilla-JS
layer for background-job polling.

**Read this file before adding a new generation/background-job tool** — the
pattern below (`stores/jobs.py` + `routers/task_helpers.py` + one router
file) is reused verbatim by seven+ tools; a new one should almost never need
new plumbing.

# Layout

```
web/
  main.py            app assembly only — middleware, static, lifespan, loop over ROUTERS
  api_keys.py        provider API-key form pre-fills (per provider, per role)
  middleware/        one concern per file, each owning its own policy: auth, ratelimit
  recovery.py        background sweeper for provider requests that outlived their poll
  schemas/           typed shapes: persistence.py (DB), generation.py (provider/model lists)
  templates_config.py  get_templates(), the shared Jinja2Templates instance
  stores/            the app's nouns: jobs, applications
  db/                the only code that touches Postgres
  routers/           every HTTP route, grouped by feature (see routers/__init__.py)
  templates/ static/ server-rendered pages and the design system
```

Three layers, one direction of dependency: **routers -> stores -> db**. A
router never opens a session or imports a repository; a store never renders
or raises `HTTPException`. That separation is why the JSON-file -> Postgres
migration changed everything under `stores/` without touching a router.

# Top-level files

- `main.py` — also the API's logging entrypoint: calls
  `ofmhelpers.log.configure_logging()` at import time (uvicorn imports this
  module to find `app`, so it runs before the first request). Otherwise it
  only assembles: middleware, static mount, `lifespan` (reloads job history
  via `stores.jobs.load_jobs()`, starts `recovery.py`'s sweeper), and one
  loop over `routers.ROUTERS`. **Adding a page does not touch this file** —
  see `routers/__init__.py`.
  Middleware order matters and reads bottom-up in the source: Starlette
  applies middleware outside-in in *added* order, so the last
  `add_middleware` call runs first. A request passes SessionMiddleware ->
  WriteRateLimitMiddleware -> AuthMiddleware.
- `middleware/auth.py` — **all of auth, on `AuthMiddleware` itself**: single
  shared password per role (`APP_PASSWORD_ADMIN`/`_VA` env vars) via
  `AuthMiddleware.check_password`, the per-request session-cookie gate in
  `dispatch`, `AuthMiddleware.require_admin` as the FastAPI dependency for
  admin-only routers, and `AuthMiddleware.visible_actor` — the VA/admin
  separation for jobs: None (everyone's) for an admin, the session's own role
  otherwise. Every job status/poll/file endpoint passes it to
  `get_job(job_id, actor)` and every gallery to `list_jobs_page(..., actor)`,
  so a VA never sees, polls or downloads an admin's job (it 404s). There is no `web/auth.py` — one place to look for
  anything auth-shaped. The allowlist (`settings.web.public_paths` /
  `public_prefixes`, read by `AuthMiddleware.is_public`) and the role names
  (`settings.web.role_admin` / `role_va`) are config, not literals — keep the
  allowlist short, anything not listed is protected by default.
  An unauthenticated request gets one of two answers, decided by `is_fetch`:
  a page navigation gets the 303 to `/login?next=...`, a fetch/XHR gets
  `401 {"login_url": ...}`. That split exists because `fetch` follows a 303
  transparently — an expired session used to hand the JS the login page's
  HTML with status 200. Session lifetime is
  `settings.session.session_max_age_s` (config, not a literal), consumed by
  `SessionMiddleware`'s `max_age` and by `static/js/session.js`.
- `api_keys.py` — `get_api_key(request, provider)` / `get_api_keys(request)`
  (every provider's pre-fill, from `Provider.default_key(admin)`).
  Deliberately not in the middleware: it decides what a form field *starts
  out containing*, not who may reach it, and they are optional by design —
  unset var means an empty field the user pastes into.
- `middleware/ratelimit.py` — fixed-window counters **and** their two
  enforcement points, one file: the login route calls `login_blocked` /
  `record_failed_login` / `clear_failed_logins` (only **failed** attempts are
  counted, so a real user is never locked out by their own traffic), and
  `WriteRateLimitMiddleware` puts a blunt per-IP ceiling on
  POST/PUT/PATCH/DELETE. Counters live on `ofmhelpers.cache`'s Redis
  connection. Redis errors fail **open** — a dead broker already
  takes the app down; turning it into a total login lockout would be worse.
  Client identity is `request.client.host`, which is only correct because
  the app publishes its port directly; behind a proxy uvicorn needs
  `--proxy-headers --forwarded-allow-ips`.
- The RQ queue is **not** here: it and the single Redis connection live in
  `ofmhelpers/cache/` (`queue.py`, `redis.py`), because the worker and the
  kie.ai client need them too. `enqueue(...)` runs jobs
  on the worker in prod; in the test suite (`OFM_RQ_ASYNC=false`) it runs them
  inline, exactly like the old BackgroundTasks, so TestClient still sees
  results immediately.
- `ref_usage.py` — which shared reference files were last *picked*, in a Redis
  sorted set on the shared Redis connection (same shared-state reasoning as
  `middleware/ratelimit.py`). It exists so `refs.py` can show "last used" and "last
  uploaded" as two different lists: reuse used to `touch()` the file, which
  made mtime mean both at once and made a resolver quietly write to the asset
  store. Not a Postgres table — this is picker ordering, not a noun the app
  owns; losing it degrades the picker to "most recently uploaded". Redis
  errors are swallowed: a broker that can't record a pick is no reason to fail
  a generation that has every file it needs.
- `reel_watch.py` — second lifespan sweeper: every
  `rescan_check_interval_s` it atomically claims watched Instagram profiles
  whose scan is older than `rescan_interval_s` (12 h) and queues **one**
  worker job scanning them sequentially (parallel logged-out calls just get
  429s). Claims expire after `rescan_claim_ttl_s`, so a job killed by a
  deploy is retried. Each profile is tried on up to 1 + `proxy_retries`
  proxies (jittered pause between tries; each call also retries drops,
  timeouts and 5xx itself, `http_retries` with backoff + jitter). Rate
  limit, 5xx, any network error and "not found" all move it to the next
  proxy; a proxy failing at the transport level (ProxyError, TLS, connect
  timeout) leaves the job's `_ProxyPool`, direct once none is left.
  **A profile with a good scan is never removed by a later one** -- "not
  found" is also what a proxy IP Instagram has flagged gets for a real
  account (that auto-drop deleted ~36 real profiles on 2026-10-17); the
  failure is recorded on the row and its reels stay. A **just-added**
  profile (no result yet) is dropped instead when its first scan finds it
  private, or not found on every try *and* the cookies.txt login's
  profile page (`InstagramClient.exists_logged_in`, `pageID`
  httpErrorPage) agrees -- verified 34/34 on 2026-10-17; reels.js reports
  the ones not added. Only every try rate-limited ends the batch (the
  rest stay due). Also what
  `POST /reels/watch` calls for new profiles.
- `recovery.py` — background sweeper (every `SWEEP_INTERVAL_S` = 300s),
  provider-agnostic: for every request `aigenproviders.tasks` still
  remembers, `settle` checks its job. Running -> left alone; done or deleted
  -> forgotten; older than `settings.aigen.resume_max_age_s` -> forgotten;
  **failed** (gave up polling) -> one `status` call on the key the request
  was made with: still running -> next sweep; failed -> the job's error
  becomes the provider's reason; done -> the job is re-run with its stored
  params via `registry.RESUMABLE` (`ai`'s `run_ai`, `clone`'s `_run_clone`),
  and the re-run resumes the same request (`runner` recalls it) instead of
  paying again, so the tool's own tail (CapCut, asset store) runs. A
  provider error skips one task, never the sweep.
- `middleware/` — one concern per file, and a concern is *whole*: `auth.py`
  (`AuthMiddleware` + the allowlist/password/role checks) and `ratelimit.py`
  (`WriteRateLimitMiddleware` + the counters and the login brake). Order is
  decided in `main.py`, and the package docstring records the resulting
  request order.
- `schemas/` — `persistence.py` holds the Pydantic v2 models
  (`Job`) that are the typed contract at the
  persistence boundary; `generation.py` holds `ProviderChoice` and
  `ModelChoice`, the provider select and the model list (`#model-catalog`
  JSON) a generation page offers, built from the provider registry and the
  catalog; `gallery.py` holds the
  gallery card every tool page renders per job (`JobCard`, `TaskCard` for
  the mixed-tool galleries, `CloneCard`/`CloneBatch` for /clone, whose
  `CloneBatch.group` regroups a page of cards into submits). Data only: the
  asset cards come in built by `task_helpers.job_assets`. Import from the
  package.
- `templates_config.py` — `get_templates()`, the shared `Jinja2Templates`
  instance every router renders through (`lru_cache`d, built on first use).
  Also the `static_url('js/x.js')` template global: **every CSS/JS link goes
  through it** (`/static/...?v=<content hash>`). nginx caches `/static` for a
  week, so a bare `/static/...` link keeps serving the pre-deploy file -- an
  old clone.js posting to the new /clone/run is exactly how that broke.

# `stores/` — the app's nouns

Plain functions over dicts, each wrapping a repository in `db/`. Routers
call these and nothing below them.

- `jobs.py` — **the core background-job pattern every generation/download
  tool uses.** `create_job(task_name, params, actor)` -> `enqueue(run_job,
  job_id, fn, kwargs)` (see `queue.py`) -> `get_job(job_id)` for polling.
  `run_job` catches exceptions and stores just the message (not a traceback)
  as `job["error"]`. Status transitions are atomic single UPDATEs. Result
  files live inside a job's `result` payload (JSONB) — there is no separate
  file-reference table. `list_jobs()` self-heals history when a result file
  was deleted on disk. Anything that renders only a page of jobs uses
  `list_jobs_page(tasks, offset, limit, actor) -> (page, total)` instead: the filter
  and the slice run in SQL (`JobRepository.list_page`, cached) and **only the
  page is healed**, because healing stats every result file it is handed — doing that
  for the whole history to paint 20 cards is what made a scroll tick cost more
  than the page it returned.
- `applications.py` — submissions of the public `/` landing-page form
  (`routers/apply.py` writes, `routers/admin/applications.py` reads).
  Write-once rows: a lead is never edited, only read or deleted.

# `db/` — the persistence layer (Postgres)

`models.py` (SQLAlchemy tables), `session.py` (lazy engine/session from
`settings.infra`), `repositories/` (**the only code that touches the DB**, one
module per domain: jobs, applications — import the classes from the
package),
plus `cached_repository.py`, the cache-aside layer +
`@cached`/`@invalidates_cache` every repository inherits). Schema changes are versioned with Alembic (`alembic/`
at the repo root). The todo / model-roster / Instagram-stats / competitor /
approval-token tables are still in Postgres with their data but no longer
mapped (those features were removed); `alembic/env.py`'s `RETIRED_TABLES`
keeps autogenerate from ever proposing to drop them.

# `routers/` — grouped by feature

`routers/__init__.py` holds `ROUTERS`, the single registration list.
**Adding a page = one import + one entry there**, never an edit to
`main.py`. URL prefixes live on each `APIRouter`, so moving a module between
packages never changes a URL.

**The shared "upload -> background job -> poll -> download" pattern**
(`task_helpers/`, at the `routers/` root because it belongs to no single
feature — `uploads.py` where files land, `manifests.py` new-vs-reused
reconciliation, `responses.py` the status payloads, `serving.py` handing files
back; import from the package, and note that `uploads.ASSETS_ROOT` is the one
seam tests move) is what makes every generation tool a thin file: `ASSETS_ROOT`
(content-addressed upload store, deduped by sha256 — split per role by
`assets_root_for(request)`: an admin owns the root, which also contains the
`va/` folder; a VA reads and writes only `va/`. Request-side code passes
`assets_root_for(request)`; a worker job gets the resolved path as an
`assets_root` kwarg, since it has no session),
`build_ordered_paths` (reconciles a JSON manifest of new+reused reference
files — never re-uploads a file the client already has, and is the one place
that calls `ref_usage.record_use`, since it is the one place a resolve means
"the user picked this"; `resolve_existing_ref` itself only validates and never
writes), `asset_card` /
`job_assets` / `job_status_payload` / `serve_job_file` / `job_inputs`
(generic response-shaping every status/polling/download endpoint reuses
verbatim). `job_assets` and `serve_job_file` handle both result shapes --
one entry per file, or grouped by source URL (the two `downloads/` tools) --
and number a grouped job's files the same way, so a card's index always
serves the file it shows. `add_job_routes` (`routes.py`) registers the three
GET endpoints of a job (status page, polling payload, file download) under a
router's prefix; every tool router calls it instead of writing them.

It also owns the two upload-safety primitives every upload route must use:
`safe_filename` (basename only — a multipart `filename` of
`"../../cookies/cookies.txt"` is legal and would otherwise write outside the
upload directory) and `require_upload_kind` (extension allowlist), plus
`media_response` for serving user files back without letting them execute in
our own origin.

**Standard tool router shape** (every module in `generation/` follows it):
`POST /<prefix>/run` creates a job and backgrounds the real work, returning
`{"job_id": ...}` immediately; `GET /<prefix>/jobs/{id}` renders
`templates/job_status.html`; `GET /<prefix>/jobs/{id}/status` returns the
JSON polling payload; `GET /<prefix>/files/{id}/{index}` streams the result
file. AI generation does not add tools: a new model is a catalog entry and
a new provider is an `aigenproviders/` subpackage, both served by
`generation/ai.py` (see `aigenproviders/CLAUDE.md`).

## `generation/` — the AI tools

- `index.py` — `/generate`: provider select, then model select (every
  model of that provider from `#model-catalog`, grouped by family), the
  shared prompt box, then the model's own fieldset, which
  `static/js/ai-form.js` loads from `GET /ai/fields?model=` on every model
  change (picked files follow the switch by kind, the prompt box is
  required / optional / hidden per model, a model's `default_prompt` fills
  it only while it holds no text of the user's), posting to `/ai/run`. Plus
  a cross-provider gallery with click-to-reuse ("↻ Recreate" restores
  provider, model, settings and reference files; a job from before /ai
  restores its prompt only). The gallery pages `gallery_limit` at a time:
  `GET /generate/gallery?offset=` returns the next page as an HTML fragment
  for `static/js/gallery-scroll.js` to append, and `_gallery_page` emits a
  next offset only while there is another page — the absence of a
  `.gallery-sentinel` in a response is what ends the scroll. Both the page
  and the fragment render each card through `_generate_gallery_card.html`,
  so an appended card is indistinguishable from a server-rendered one. The
  tasks it lists are `registry.GENERATE_TOOLS`.
- `ai.py` — `/ai`, every model of every provider, task `ai`.
  `GET /ai/fields` renders `_ai_fields.html` from the spec (enum -> select,
  number with min/max, boolean as a hidden "false" + checkbox, one
  `_file_picker` per media field — `max=1` for a single-file one — the
  prompt skipped: it is the page's own box). `POST /ai/run` reads the raw
  form (an async dependency, so the route itself stays sync and off the
  event loop): `provider` / `model` / `api_key` (`AiRunForm`), the spec's
  scalars typed by `spec.parse_form`, each media picker through
  `build_ordered_paths` (the raw form holds Starlette's `UploadFile`, not
  FastAPI's subclass), then `spec.check` — a bad request is a 400 naming the
  field, before anything is uploaded or paid. Params stored: `provider`,
  `model` (the catalog key) and the inputs under the schema's own names,
  media as lists of asset-store paths (which is what Recreate restores and
  `job_inputs` previews). The key goes to the worker only. `run_ai` is the
  worker side: upload the references through the provider, then
  `aigenproviders.runner.generate`; the hosted result is the job preview
  the moment it exists, a failed local download keeps the job done on the
  hosted copy, results are registered in the asset store. `describe(job)`
  labels its gallery card "<model> · <provider>".
- `legacy.py` — the job routes (status page, polling, files) of the
  per-model routers `ai.py` replaced (`/seedance`, `/kling3`, ...,
  `/fake-ai`), no `/run`, so old gallery cards and links keep working; the
  registry lists those task names label-only (not resumable).
- `clone.py` — `/clone`, both roles, own page: 1..`clone_max_images` subject
  images (same person, sent to every run) + N videos
  (two file pickers, so reuse/Recreate never re-uploads) -> one batch of
  `clone` jobs, each a Wan 3.0 reference-to-video run (`ai.run_ai`) on the
  picked provider. `CLONE_TARGETS` names, per provider, its Wan 3.0 model and
  which schema fields take the subject, the video and the audio switch (kie
  `wan/3-0-video`, Higgsfield `alibaba/wan-3.0/reference-to-video`); a
  provider joins /clone by one entry there. Params stay provider-neutral
  (`reference_images`, `reference_videos`, `resolution` as 480P/720P/1080P
  — sent in each model's own spelling), so the page and Recreate read
  every batch alike. Each run's duration is its video's length
  (`utils.video_probe`, rounded, clamped to the model's schema range). Every
  video is probed and every run `spec.check`ed before any job is queued, and
  `clone_max_videos` caps a batch, so a bad submit spends nothing. Jobs share `clone_batch` +
  `clone_index` params; `schemas.CloneBatch.group` regroups them for the
  page (one hue per batch). `/run` answers with the new
  batch rendered through `_clone_batch.html` (the page's own partial), which
  `static/js/clone.js` inserts in place -- no reload -- with XHR upload
  progress. Not in `/generate`'s gallery. The "CapCut edit + export"
  checkbox (`capcut`, on by default in the form; a job without the param
  runs without it) re-encodes each generated video through
  `utils.capcut.DEFAULT_PRESET` on the worker; the `_capcut.mp4` copy
  becomes the result with `remote_url` dropped, so view/download serve it
  rather than the provider's original (which stays on disk and in the
  asset store). A remote-only result (local download failed) is left as is.
  Above Start, `static/js/clone-cost.js` prices the pick live for the
  provider picked: each video's length (read from its metadata in the
  browser) x that provider's credits per second of source video at the
  resolution x its dollars per credit, from `clone_pricing()` (one
  `ClonePrice` per provider, handed to the page as `data-pricing`). Both
  providers bill output + reference seconds, so about 2x the source
  length. kie.ai: `settings.web.clone_credits_per_video_s`, measured from
  this account's history, x `kie_usd_per_credit` ($50 = 10,000 credits).
  Higgsfield: `clone_hf_credits_per_video_s` x `hf_usd_per_credit`
  (16 credits = $1) -- its list price at this account's 60% discount,
  measured 2026-10-18 (a 13.09 s source at 720p billed 26.09 s, $1.05;
  Higgsfield's API never reports a charge or a balance, and its estimate
  endpoint gives Wan 3.0's list rate card only). A video over the
  provider's `max_reference_s` (kie.ai 15; Higgsfield none known) is
  flagged and left out of the total; it watches the picker through
  file-picker.js's `filepicker:change` event. A provider with no price
  hides the panel.
- The no-cost stand-in is a provider now (`aigenproviders/fake/`, "Fake AI
  (testing)" in the provider select): same router, runner and result
  handling as a real one, so the e2e suite exercises the real pipeline.

## `downloads/` — pulling media in

- `index.py` — unified tool-picker page for the three below, same style as
  `generation/index.py`.
- `videos.py` (prefix `/download-videos`) / `images.py` — bulk downloaders
  using `downloaders.generic`/`downloaders.images`, the grouped-by-source-URL
  result shape.
- `clean_image.py` — strips image metadata (`utils.metadata_cleaner`).
- `reels.py` — `/reels`, both roles, own page: a watch list of Instagram
  profiles, each in an optional **group** (`stores/watched_profiles.py`,
  `watched_profiles.group_name`; a group made with "+ New group" before any
  profile is in it lives in `profile_groups`, `POST /reels/groups`; a row's
  dropdown moves a profile, `POST /reels/group/{username}`), rows
  `_reels_watch_item.html`, ordered by `schemas.Leaderboard` for the
  filtered group: the most window views first (gold, "#1 most views"), the
  most feed reels second (orange, "#2 most viral reels"), the rest most feed
  reels first (`WatchedProfile.by_reels`), and **one flat feed** of their latest
  scans (`schemas.FeedItem.of`, cards `_reel_card.html`), most viewed first,
  filtered by `?group=` and `?rank=` (a `VIEW_TIERS` name: exactly that
  rank; common 50k, uncommon 100k, rare 200k, epic 500k, legendary 1M+), paged
  `feed_page_size` (60) at a time through `GET /reels/feed` +
  `static/js/gallery-scroll.js` (which now accepts an endpoint carrying its
  own query). A scan (`instagram.scan_profile`) keeps reels from the last
  `max_age_days` with >= `min_views` views and <= `max_duration_s` (30 s,
  Wan 3.0's max). Card border + ratio pill = view tier
  (`instagram.models.VIEW_TIERS`). `POST /reels/watch` (`schemas.WatchForm`:
  first token per line, so "@name — Real Name" works; one bad line rejects
  the paste) adds new profiles to the group, moves already-watched ones to
  it, and calls `reel_watch.queue_due_scans()` -- scans never run in a
  request; the page polls `GET /reels/watchlist` (`data-pending` rows) and
  reloads when none is left. The page holds only lazy `<img>` thumbnails;
  `reels.js` creates the `<video>` (CDN mp4, sound once the page has had a
  click) on first hover. Picks live in `sessionStorage` (code -> mp4 URL),
  so they survive paging, group switches and reloads. Nothing is downloaded
  until `POST /reels/clone`, which saves the picked reels into the asset
  store and answers `/clone?video=...` — `clone.py` validates those against
  the session's store and pre-fills the videos picker. `POST /reels/download`
  (Download selected) fetches the same picks and answers the mp4 itself for
  one, a stored zip for several; nothing enters the asset store. See
  `instagram/CLAUDE.md` for how Instagram is reached and why.

## `image_tools/` — local image edits

- `character_sheet.py` — `/character-sheet` (admin only, router-level
  `require_admin`), its own page + gallery: 2 to
  `settings.web.character_sheet_max_images` uploads stitched side by side
  (`utils.character_sheet`) into one lossless PNG, registered in the asset
  store. Uploads are saved as `NN_<name>` so upload order survives and iOS's
  identical `image.jpg` names cannot overwrite each other.

## `video_tools/` — local video edits

- `uniqify.py` — `/uniqify`, both roles, own page + gallery: one uploaded
  video re-encoded into 1..`settings.web.uniqify_max_copies` variants
  (`utils.uniqify`), named `<output name>_001.mp4`…, sequentially on one
  worker job, then bundled into `<output name>.zip` (stored, not deflated)
  and the loose copies deleted — the zip is the job's only result. Not added
  to the asset store. `generation.js` renders a non-media result like the zip
  as a file card, matching `_asset_media.html`'s fallback.

## `admin/` — admin-only surfaces

Every router here is gated at the router level with
`dependencies=[Depends(AuthMiddleware.require_admin)]`, so a new endpoint added to these
files is admin-only by default.

- `file_manager.py` — browse/download/delete under `uploads/`/`downloads/`/
  `kieai_out/`. `_safe_path` resolves and refuses to leave the chosen root.
- `action_log.py` — audit log of every job across every task type
  (`TASK_STATUS_PREFIX` maps a job's task name to its status-page URL prefix
  — add an entry for any new task type, same idea as `generation/index.py`'s
  registry but repo-wide).
- `applications.py` — `/applications`: the CRM table of every public
  landing-page application (`stores/applications.py`), newest first, read +
  delete only.
- `cookies.py` — upload endpoint for `cookies/cookies.txt`
  (`downloaders.cookies`).

## At the `routers/` root

- `auth.py` — `/login`, `/logout`.
- `apply.py` — `POST /apply`, the only public *write* in the app: the
  application form on the `/` landing page (`templates/reachmodel.html`).
  Public via `settings.web.public_paths`, still capped per IP by
  `WriteRateLimitMiddleware`. Post/Redirect/Get back to `/?applied=1`, so a
  refresh cannot submit twice.
- `refs.py` — serves/lists previously-uploaded reference files from the
  session's part of the asset store (`assets_root_for`) for the file-picker
  widget's "reuse" browser; `/refs/file` and `/refs/thumb` 403 a path outside
  it. `GET /refs` with
  no `limit` returns **two short lists**: the `RECENT_USED_LIMIT` (5) files you
  last *picked* (`web/ref_usage.py`), then the `RECENT_UPLOAD_LIMIT` (5) most
  recently *uploaded* ones not already in it, each entry carrying `used_at`
  (None for the second group) so the picker can label them. Those are two
  genuinely different orderings: mtime means "uploaded" and nothing rewrites
  it, so a file you use daily can't sink out of view and a fresh upload can't
  be hidden by something you picked once. An explicit `?limit=N` (the "Show
  older" button, capped at `MAX_REF_LIMIT` = 60) is plain newest-uploaded
  first, no grouping.
  `write_image_thumb` is the Pillow thumbnailer behind the picker previews.
- `task_helpers/` — the shared plumbing described above.

# `templates/` and `static/`

Server-rendered Jinja2, extending `base.html` (sticky glass header +
role-aware nav — add a new top-level page's link in `base.html`'s
`nav_items` list; it also feeds the footer). Shared partials:
`_file_picker.html` (multi-file ordered picker macro; `max=` caps it, newest
winning, `multiple=` lets the dialog pick several at once),
`_provider_fields.html` (provider select + its API key, swapped per
provider; emits `provider:change`), `_ai_fields.html` (one model's fields,
see `ai.py`), `_asset_grid.html`/`_asset_media.html` (result
rendering), `job_status.html` (the generic status/polling page every
standard-shape tool reuses),
`_generate_gallery_card.html`/`_generate_gallery_sentinel.html` +
`generate_gallery_fragment.html` (the paged `/generate` gallery — the card
partial is shared by the page and the fragment so the two can't drift, and its
JS twin is `generation.js`'s `buildResultCard`).
Note `_asset_media.html` renders no filename of its own: `_asset_grid.html`
adds one for every kind, so a template calling `asset_media()` directly has to
add its own `<p class="filename">` — an `<audio>` element shows nothing
identifying otherwise.

**`static/css/app.css` is the whole design system** — tokens (colour,
spacing, fluid type scale, motion) then base, layout, components. A page
template should reach for the existing components instead of a `<style>`
block: `.page-head` + `.lead` (page title), `.card`/`.card-grid`,
`.link-card`, `.btn` + `.btn-primary`/`-outline`/`-danger`/`-ghost`,
`.field`, `.table-wrap` (tables scroll instead of squashing on a phone),
`.badge`, `.notice`, `.empty-state`, `.results`/`.result-item`.
The legacy per-page names (`.root-btn`,
`.download-btn`, …) are aliased onto `.btn` rather than re-declared, so
buttons cannot drift apart again. A new `<style>` block in a template means
this file is missing a component — add it there. Class names the JS builds
or queries (`.result-item`, `.ref-tile`, `.file-order-list`,
`.prompt-backdrop`, `.session-expired-*`, `.spinner`, …) are contract.

Layout is mobile-first: unprefixed rules are the phone layout, `min-width`
queries add the wider ones. `static/js/nav.js` only drives the small-screen
nav drawer (`aria-expanded` + `.open`); at ≥980px the CSS puts the links
back in a row and hides the toggle, so the desktop nav needs no JS at all.

`static/js/ai-form.js` drives /generate's provider -> model -> fields form
(see `index.py`). `static/js/generation.js` is the shared "submit -> poll ->
render inline" controller: any `<form data-prefix="..." data-result-kind="video|image">`
auto-wires on page load, POSTs to `form.action`, then polls
`${prefix}/jobs/${job_id}/status` and swaps the pending card for the result
(or error) in place — no page navigation. `static/js/file-picker.js` is the
reference-file picker widget (`FilePicker.collectFormData`; `FilePicker.init` wires pickers inserted
after load); works with the manifest reuse pattern in
`task_helpers.build_ordered_paths`.
`static/js/session.js` is loaded in `base.html`'s `<head>` **before every
other script** and wraps `window.fetch` once, globally: any `401` carrying a
`login_url` shows a brief "Session expired" overlay and redirects the tab to
`/login?next=...`. That's why no individual call site needs 401 handling —
`generation.js`'s poller and
`file-picker.js` all inherit it. It also arms a wall-clock (not
`setTimeout`-duration, so a suspended laptop still expires correctly) idle
timer from `base.html`'s `data-session-max-age`, so an untouched tab logs
itself out instead of sitting there looking signed in.
`static/js/prompt-highlight.js` highlights `[Image1]`/`@audio1`-style
reference markers in a prompt textarea (only relevant to tools that use
that marker convention, e.g. `generation/index.py`'s unified form).
`static/js/gallery-scroll.js` is the infinite scroll: an
`IntersectionObserver` on `.gallery-sentinel[data-next-offset]` inside any
`[data-gallery-endpoint]` container fetches the next page of cards as a
fragment and swaps the sentinel for it. It calls
`Generation.resumePendingCards()` afterwards (idempotent — `data-resumed`
marks cards already being polled) so an appended still-running card resolves
inline. `wireDownloadButtons` needs no such call: it is one delegated document
listener, so appended cards are covered already.
`static/js/live-gallery.js` keeps `/generate` and `/clone` in sync across
sessions: a `[data-live-endpoint]` container polls every
`gallery_live_poll_s` (`/generate/gallery/latest`, `/clone/batches` — the
pages' own partials) and prepends items it lacks, keyed by `data-live-key`
(`jobId` / `batch`). Paused while the tab is hidden. Own submits dedupe
against it in `generation.js` / `clone.js`.
