# Module purpose

The FastAPI web app ("Global Ascend LLC — Content Ops"): a single-password,
two-role (admin/VA) internal tool for AI content generation (the kie.ai
models), media downloading/cleaning, the landing-page application CRM, and
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
  api_keys.py        provider API-key form pre-fills (kie.ai per role)
  middleware/        one concern per file, each owning its own policy: auth, ratelimit
  recovery.py        background sweeper for orphaned kie.ai generations
  schemas/           typed shapes: persistence.py (DB), generation.py (forms)
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
  `dispatch`, and `AuthMiddleware.require_admin` as the FastAPI dependency for
  admin-only routers. There is no `web/auth.py` — one place to look for
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
- `api_keys.py` — `get_kie_api_key(request)` (per-role pre-fill). Deliberately
  not in the middleware: it decides what a form field *starts out containing*,
  not who may reach it, and they are optional by design — unset var means an
  empty field the user pastes into.
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
- `recovery.py` — background sweeper (every `SWEEP_INTERVAL_S` = 300s)
  calling `KieAIClient.resume_pending()` for every configured kie.ai API
  key, so an in-request poll timeout or a server restart mid-generation
  still gets downloaded automatically.
- `middleware/` — one concern per file, and a concern is *whole*: `auth.py`
  (`AuthMiddleware` + the allowlist/password/role checks) and `ratelimit.py`
  (`WriteRateLimitMiddleware` + the counters and the login brake). Order is
  decided in `main.py`, and the package docstring records the resulting
  request order.
- `schemas/` — `persistence.py` holds the Pydantic v2 models
  (`Job`) that are the typed contract at the
  persistence boundary; `generation.py` holds `ReferenceUploads`, the
  three-picker form shape seedance, wan, minimax and fake_ai share (resolved to paths by
  `routers/task_helpers.resolve_reference_uploads`). Import from the package.
- `templates_config.py` — `get_templates()`, the shared `Jinja2Templates`
  instance every router renders through (`lru_cache`d, built on first use).

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
  `list_jobs_page(tasks, offset, limit) -> (page, total)` instead: the filter
  and the slice run off the cached repository read and **only the page is
  healed**, because healing stats every result file it is handed — doing that
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
`@cached`/`@invalidates_cache` every repository inherits),
`backfill_remote_urls.py` (one-time, manually-run: re-derives kie.ai
`remote_url` for old jobs that predate that field — `--apply` to write,
dry-run by default). Schema changes are versioned with Alembic (`alembic/`
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
(content-addressed shared upload store, deduped by sha256),
`build_ordered_paths` (reconciles a JSON manifest of new+reused reference
files — never re-uploads a file the client already has, and is the one place
that calls `ref_usage.record_use`, since it is the one place a resolve means
"the user picked this"; `resolve_existing_ref` itself only validates and never
writes), `asset_card` /
`job_status_payload` / `serve_job_file` / `job_inputs` (generic
response-shaping every status/polling/download endpoint reuses verbatim).
`flatten_grouped_results` / `grouped_job_status_payload` is the sibling
pattern for tools whose result is grouped by source URL (the two
`downloads/` tools) rather than one-file-per-job.

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
file. Adding a generation backend = write `_run_<tool>(...)` calling that
backend + these five endpoints wired to `task_helpers`, nothing else.

## `generation/` — the AI tools

- `index.py` — the unified tool-picker page (`/generate`): one form whose
  fieldset switches between seedance/seedance25/kling3/wan3/minimax_h3/nanobanana/
  seedream45/seedream5/gpt_image/fake_ai, plus a
  cross-tool gallery with click-to-reuse. `TASK_LABELS`/
  `FILES_PREFIX` here are the central registry — **add an entry here for any
  new job task name that should show up in this gallery.**
  The gallery pages `gallery_limit` at a time: `GET /generate/gallery?offset=`
  returns the next page as an HTML fragment for `static/js/gallery-scroll.js`
  to append, and `_gallery_page` emits a next offset only while there is
  another page — the absence of a `.gallery-sentinel` in a response is what
  ends the scroll, so there is no `has_more` flag that could disagree with the
  cards actually returned. Both the page and the fragment render each card
  through `_generate_gallery_card.html`, so an appended card is
  indistinguishable from a server-rendered one (the delegated Recreate and
  Download handlers and the resumed poller all key off its attributes).
- One module per kie.ai model — `seedance.py` (2.0) / `seedance25.py` /
  `kling.py` / `wan.py` / `minimax.py` / `nbp.py` / `seedream45.py` /
  `seedream5.py` / `gpt_image.py` — each only its `/run` form and the
  `KieAIClient.generate_*` call it wraps. Everything they share lives once in
  `kie_jobs.py`: `start_kie_job` (create + enqueue), `run_kie_generation`
  (preview, remote-only fallback, asset registration), `upload_references`,
  and `add_job_routes` (the three GET endpoints). Allowed values come from
  `aigenproviders/kaiai/types.py`, typed on the form so a bad value is a 422.
- `fake_ai.py` — a no-cost stand-in with the exact same shape (same
  `OUT_DIR`/`ASSETS_ROOT`), for exercising the upload/poll/gallery plumbing
  without spending kie.ai credits or waiting on a real provider.

## `downloads/` — pulling media in

- `index.py` — unified tool-picker page for the three below, same style as
  `generation/index.py`.
- `videos.py` (prefix `/download-videos`) / `images.py` — bulk downloaders
  using `downloaders.generic`/`downloaders.images`, the grouped-by-source-URL
  result shape.
- `clean_image.py` — strips image metadata (`utils.metadata_cleaner`).

## `image_tools/` — local image edits

- `character_sheet.py` — `/character-sheet`, its own page + gallery: 2 to
  `settings.web.character_sheet_max_images` uploads stitched side by side
  (`utils.character_sheet`) into one lossless PNG, registered in the asset
  store. Uploads are saved as `NN_<name>` so upload order survives and iOS's
  identical `image.jpg` names cannot overwrite each other.

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
- `refs.py` — serves/lists previously-uploaded reference files from
  `ASSETS_ROOT` for the file-picker widget's "reuse" browser. `GET /refs` with
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
`_file_picker.html` (multi-file ordered picker macro),
`_kie_api_key_field.html`, `_asset_grid.html`/`_asset_media.html` (result
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

`static/js/generation.js` is the shared "submit -> poll -> render inline"
controller: any `<form data-prefix="..." data-result-kind="video|image">`
auto-wires on page load, POSTs to `form.action`, then polls
`${prefix}/jobs/${job_id}/status` and swaps the pending card for the result
(or error) in place — no page navigation. `static/js/file-picker.js` is the
reference-file picker widget (`FilePicker.collectFormData`); works with the
manifest reuse pattern in `task_helpers.build_ordered_paths`.
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
