# Module purpose

The FastAPI web app ("Global Ascend LLC — Content Ops"): a single-password,
two-role (admin/VA) internal tool for AI content generation (Seedance,
Kling, Nano Banana Pro, reel cloning), media downloading/cleaning, scraping,
a VA todo list with Discord/Drive approval handoff, and file management. No
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
  auth.py            who you are: password check, session gate, require_admin
  ratelimit.py       how often you may ask: login brake + write ceiling
  queue.py           RQ handoff to the worker container
  recovery.py        background sweeper for orphaned kie.ai generations
  schemas.py         Pydantic mirror of the persisted shapes
  templates_config.py  the shared Jinja2Templates instance
  stores/            the app's nouns: jobs, todos, models, instagram_stats, approval_tokens
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
- `auth.py` — single shared password per role (`APP_PASSWORD_ADMIN`/`_VA`
  env vars), one `AuthMiddleware` that gates every request via a signed
  session cookie. `PUBLIC_PATHS`/`PUBLIC_PREFIXES` allowlist unauthenticated
  routes (keep this short — anything not listed is protected by default).
  An unauthenticated request gets one of two answers, decided by `is_fetch`:
  a page navigation gets the 303 to `/login?next=...`, a fetch/XHR gets
  `401 {"login_url": ...}`. That split exists because `fetch` follows a 303
  transparently — an expired session used to hand the JS the login page's
  HTML with status 200. Session lifetime is
  `settings.session.session_max_age_s` (config, not a literal), consumed by
  `SessionMiddleware`'s `max_age` and by `static/js/session.js`.
  `get_kie_api_key(request)` pre-fills the kie.ai API key field based on
  role. `require_admin` is a FastAPI dependency for admin-only routers.
- `ratelimit.py` — fixed-window counters on the RQ Redis connection. Two
  users: the login route counts **failed** attempts only (a correct password
  clears the counter, so a real user is never locked out by their own
  traffic), and `WriteRateLimitMiddleware` puts a blunt per-IP ceiling on
  POST/PUT/PATCH/DELETE. Redis errors fail **open** — a dead broker already
  takes the app down; turning it into a total login lockout would be worse.
  Client identity is `request.client.host`, which is only correct because
  the app publishes its port directly; behind a proxy uvicorn needs
  `--proxy-headers --forwarded-allow-ips`.
- `queue.py` — the RQ queue (Redis) the API enqueues onto and the `worker`
  container consumes, replacing FastAPI BackgroundTasks. `enqueue(...)` runs
  jobs on the worker in prod; in the test suite (`OFM_RQ_ASYNC=false`) it runs
  them inline, exactly like the old BackgroundTasks, so TestClient still sees
  results immediately.
- `recovery.py` — background sweeper (every `SWEEP_INTERVAL_S` = 300s)
  calling `KieAIClient.resume_pending()` for every configured kie.ai API
  key, so an in-request poll timeout or a server restart mid-generation
  still gets downloaded automatically.
- `schemas.py` — Pydantic v2 models (`Job`/`Todo`/`ApprovalToken`), the typed
  contract at the persistence boundary.
- `templates_config.py` — the shared `Jinja2Templates` instance every router
  imports.

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
  was deleted on disk.
- `todos.py` — persisted VA task list (model name, link to replicate,
  comments). Durable across restarts — losing outstanding tasks would be a
  real problem, unlike losing job *history*.
- `approval_tokens.py` — single-use "magic link" tokens (no login needed)
  for approving a VA-uploaded asset, used by `routers/workflow/approve.py`.
  Snapshots the asset path it was issued for; `consume()` reports "stale"
  rather than approving the wrong file if the asset was replaced after the
  link went out.
- `models.py` — the model roster (name/picture/OnlyFans link, Instagram
  accounts, contacts, competitor profiles).
- `instagram_stats.py` — the follower/last-N-reels numbers the `/models`
  page shows, written by `scraping.instagram_stats_job`.

# `db/` — the persistence layer (Postgres)

`models.py` (SQLAlchemy tables), `session.py` (lazy engine/session from
`settings.infra`), `repository.py` (**the only code that touches the DB**),
`cache.py` (the cache-aside layer + `@cached`/`@invalidates_cache`),
`backfill_remote_urls.py` (one-time, manually-run: re-derives kie.ai
`remote_url` for old jobs that predate that field — `--apply` to write,
dry-run by default). Schema changes are versioned with Alembic (`alembic/`
at the repo root).

# `routers/` — grouped by feature

`routers/__init__.py` holds `ROUTERS`, the single registration list.
**Adding a page = one import + one entry there**, never an edit to
`main.py`. URL prefixes live on each `APIRouter`, so moving a module between
packages never changes a URL.

**The shared "upload -> background job -> poll -> download" pattern**
(`task_helpers.py`, at the `routers/` root because it belongs to no single
feature) is what makes every generation tool a thin file: `ASSETS_ROOT`
(content-addressed shared upload store, deduped by sha256),
`build_ordered_paths` (reconciles a JSON manifest of new+reused reference
files — never re-uploads a file the client already has), `asset_card` /
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
`POST /<prefix>/run` (or `/intake` + `/generate` for a two-stage flow like
replicate) creates a job and backgrounds the real work, returning
`{"job_id": ...}` immediately; `GET /<prefix>/jobs/{id}` renders
`templates/job_status.html`; `GET /<prefix>/jobs/{id}/status` returns the
JSON polling payload; `GET /<prefix>/files/{id}/{index}` streams the result
file. Adding a generation backend = write `_run_<tool>(...)` calling that
backend + these five endpoints wired to `task_helpers`, nothing else.

## `generation/` — the AI tools

- `index.py` — the unified tool-picker page (`/generate`): one form whose
  fieldset switches between seedance/kling3/nanobanana/fake_ai, plus a
  20-item cross-tool gallery with click-to-reuse. `TASK_LABELS`/
  `FILES_PREFIX` here are the central registry — **add an entry here for any
  new job task name that should show up in this gallery.**
- `seedance.py` / `kling.py` / `nbp.py` — Seedance 2.0 / Kling 3.0 / Nano
  Banana Pro generation via `KieAIClient`, following the standard tool shape.
- `fake_ai.py` — a no-cost stand-in with the exact same shape (same
  `OUT_DIR`/`ASSETS_ROOT`), for exercising the upload/poll/gallery plumbing
  without spending kie.ai credits or waiting on a real provider.
- `replicate.py` — reel-cloning pipeline (`/replicate`), see
  `reel_machine/CLAUDE.md`. Two job task types share this one router,
  dispatched by `job["task"]`: `"replicate_intake"` (download/frames/
  transcribe/draft-script, rendered by `replicate_review.html`) and
  `"replicate"` (the final Seedance generation, rendered by the standard
  `job_status.html`).

## `downloads/` — pulling media in

- `index.py` — unified tool-picker page for the three below, same style as
  `generation/index.py`.
- `videos.py` (prefix `/download-videos`) / `images.py` — bulk downloaders
  using `downloaders.generic`/`downloaders.images`, the grouped-by-source-URL
  result shape.
- `clean_image.py` — strips image metadata (`utils.metadata_cleaner`).

## `helpers/` — the standalone tools on `/helpers`

- `registry.py` — `HELPERS: list[HelperEntry]`. Add one entry for a new
  helper; `index.py` renders the page generically.
- `index.py` — the `/helpers` index.
- `elevenlabs.py` (prefix `/helpers/elevenlabs`) — ElevenLabs TTS.
- `radio_comms.py` — radio-comms audio FX (`utils.radio_comms_fx`).
- `scraper.py` — Instagram/TikTok scraper pipeline (`config.scrapers`,
  `scraping.*`), exports/filters spreadsheets.

## `admin/` — admin-only surfaces

Every router here is gated at the router level with
`dependencies=[Depends(require_admin)]`, so a new endpoint added to these
files is admin-only by default.

- `models.py` — the roster of models (name/picture/OnlyFans link + many
  Instagram accounts, each with optional owner/phone/SIM/password/email
  details, + free-form contacts), plus the Instagram stats shown inline on
  `/models`: `POST /models/refresh-stats` enqueues
  `scraping.instagram_stats_job.collect_all_instagram_stats` and answers
  `{"job_id": ...}`; the page polls `GET /models/refresh-stats/{job_id}` and
  swaps in `GET /models/stats-html` without reloading. Profile pictures are
  served as cached webp thumbs (`GET /models/{id}/picture/thumb?size=`) —
  never the original upload, which is whatever multi-MB file came off a
  phone.
- `competition.py` — `/competition`: one row per model and a column of
  competing Instagram profile links to scroll daily, add/delete only. Reads
  the same roster store and reuses `_models_style.html`, so it can't drift
  from the Models page's look.
- `file_manager.py` — browse/download/delete under `uploads/`/`downloads/`/
  `kieai_out/`. `_safe_path` resolves and refuses to leave the chosen root.
- `action_log.py` — audit log of every job across every task type
  (`TASK_STATUS_PREFIX` maps a job's task name to its status-page URL prefix
  — add an entry for any new task type, same idea as `generation/index.py`'s
  registry but repo-wide).
- `cookies.py` — upload endpoint for `cookies/cookies.txt`
  (`downloaders.cookies`).

## `workflow/` — the VA task loop

- `todo.py` — admin-managed VA task list (add/toggle/delete/export/import
  admin-only, enforced server-side per route because both roles can reach
  the page; VAs see it and upload a "ready asset", which triggers a Discord
  notification carrying a magic-link approval button).
- `approve.py` — public (no-login, outside `AuthMiddleware` via
  `PUBLIC_PREFIXES`) magic-link approval endpoint for `todo.py`'s uploaded
  assets. Read `web/auth.py`'s allowlist before adding anything here.

## At the `routers/` root

- `auth.py` — `/login`, `/logout`.
- `refs.py` — serves/lists previously-uploaded reference files from
  `ASSETS_ROOT` for the file-picker widget's "reuse" browser.
  `write_image_thumb` is the shared Pillow thumbnailer (also used by the
  models router).
- `task_helpers.py` — the shared plumbing described above.

# `templates/` and `static/`

Server-rendered Jinja2, extending `base.html` (sticky glass header +
role-aware nav — add a new top-level page's link in `base.html`'s
`nav_items` list; it also feeds the footer). Shared partials:
`_file_picker.html` (multi-file ordered picker macro),
`_kie_api_key_field.html`, `_asset_grid.html`/`_asset_media.html` (result
rendering), `job_status.html` (the generic status/polling page every
standard-shape tool reuses).

**`static/css/app.css` is the whole design system** — tokens (colour,
spacing, fluid type scale, motion) then base, layout, components. A page
template should reach for the existing components instead of a `<style>`
block: `.page-head` + `.lead` (page title), `.card`/`.card-grid`,
`.link-card`, `.btn` + `.btn-primary`/`-outline`/`-danger`/`-ghost`,
`.field`, `.table-wrap` (tables scroll instead of squashing on a phone),
`.badge`, `.notice`, `.empty-state`, `.results`/`.result-item`, `.modal`.
The legacy per-page names (`.model-btn`, `.todo-io-btn`, `.root-btn`,
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
`generation.js`'s poller, `todo_form.html`, `replicate_form.html` and
`file-picker.js` all inherit it. It also arms a wall-clock (not
`setTimeout`-duration, so a suspended laptop still expires correctly) idle
timer from `base.html`'s `data-session-max-age`, so an untouched tab logs
itself out instead of sitting there looking signed in.
`static/js/prompt-highlight.js` highlights `[Image1]`/`@audio1`-style
reference markers in a prompt textarea (only relevant to tools that use
that marker convention, e.g. `generation/index.py`'s unified form).
