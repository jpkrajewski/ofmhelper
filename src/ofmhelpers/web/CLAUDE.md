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
  api_keys.py        provider API-key form pre-fills (kie.ai per role, ElevenLabs)
  middleware/        one concern per file, each owning its own policy: auth, ratelimit
  recovery.py        background sweeper for orphaned kie.ai generations
  schemas/           typed shapes: persistence.py (DB), generation.py (forms)
  templates_config.py  get_templates(), the shared Jinja2Templates instance
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
- `api_keys.py` — `get_kie_api_key(request)` (per-role pre-fill) and
  `get_elevenlabs_api_key()` (one workspace key, role-blind). Deliberately not
  in the middleware: they decide what a form field *starts out containing*,
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
  `ofmhelpers/cache/` (`queue.py`, `redis.py`), because the worker, the
  scraping jobs and the kie.ai client need them too. `enqueue(...)` runs jobs
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
  (`Job`/`Todo`/`ApprovalToken`) that are the typed contract at the
  persistence boundary; `generation.py` holds `ReferenceUploads`, the
  three-picker form shape seedance and fake_ai share (resolved to paths by
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
`settings.infra`), `repositories/` (**the only code that touches the DB**, one
module per domain: jobs, todos, models, instagram_stats, approval_tokens —
import the classes from the package),
plus `cached_repository.py`, the cache-aside layer +
`@cached`/`@invalidates_cache` every repository inherits),
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
- `seedance.py` / `kling.py` / `nbp.py` — Seedance 2.0 / Kling 3.0 / Nano
  Banana Pro generation via `KieAIClient`, following the standard tool shape.
- `fake_ai.py` — a no-cost stand-in with the exact same shape (same
  `OUT_DIR`/`ASSETS_ROOT`), for exercising the upload/poll/gallery plumbing
  without spending kie.ai credits or waiting on a real provider.
- `replicate.py` — reel-cloning pipeline (`/replicate`), see
  `reel_machine/CLAUDE.md`. The form takes a reel URL or an uploaded file
  plus an optional free-text **Context** note (appended to the end of the
  analysis prompt, see `reel_machine/prompts.load_analysis_prompt`) — and
  nothing else. No shape/look/gender/persona/provider fields; the model
  reads all of that off the video, and the provider is an env-var
  deployment choice. The form page also lists the latest `INTAKE_LIST_LIMIT`
  (20) `replicate_intake` jobs, flagging the done-but-didn't-validate ones so
  they can be reopened and
  fixed, and every row (plus a failed review page) links to
  `/replicate?from=<job_id>` — the same form with that job's Context note
  already typed in and a `reuse_job_id` hidden field. A rerun re-analyzes the
  **file that job already downloaded** (`_downloaded_video`: the recorded
  `video_path`, else a scan of its work dir, since a job that died in analysis
  never recorded one) and only falls back to re-fetching the original link if
  that file is gone — the links that are hard to download once are exactly the
  ones that won't cooperate twice. The review page also renders
  the two hunts the VA does by hand around the generation, both pre-typed off
  the analysis by the shared `_searches(queries, engines)`:
  `_outfit_searches` (Pinterest/Google Images) and `_reel_searches`
  (Instagram/TikTok). Each takes the free model's second-pass ideas first
  (`result["hunt"]`, see `reel_machine/hunt.py`) and falls back to terms
  derived from the analysis itself (`environment` + the **main subject's**
  wardrobe via `_subject`, mirroring `ReelAnalysis.subject`; `context` +
  `viral_factor`) — every deployment does not have a `GROQ_API_KEY`, and jobs
  from before this existed have no `hunt` at all, so the derived terms stay as
  the floor. `_instagram_topic_links` turns `hunt.instagram_topics` into
  `instagram.com/popular/<slug>` pages (`/popular/baseball-girl`); those only
  ever come from the free model, since a topic slug can't be sliced out of
  prose, so with no `hunt` there is no Instagram row at all. Bare
  `instagram.com/popular/` is not linked — with no slug it is a generic
  signed-out landing page. **Instagram's own keyword search and
  `/explore/tags/` are deliberately not linked**: Instagram stopped serving
  those logged out in 2024, so every one of them opened a login wall, which is
  what made the Instagram side useless. Its reels are still publicly indexed,
  so `_REEL_ENGINES` reaches them through a `site:instagram.com/reel/` Google
  search instead. Outfit queries are prefixed with "girl"
  (`_as_womens_outfit`) — a bare clothing description comes back as menswear
  and flat-lays. Both blocks disappear when validation failed: no analysis, no
  niche. The Stage 2 form's `script` is run through `_minify_prompt_json`
  before it is stored or sent: a `<textarea>` submits its value with newlines
  normalized to CRLF, so the pretty-printed JSON the review page shows used to
  reach Seedance as a blob full of `\r\n`. It also drops every null at any
  depth (`_drop_nulls`) — the analysis prompt asks for nulls as a signal to
  itself ("line: null if no dialogue", "pose: null if off camera"), so a
  correct answer still carries one on most `scene_events` entries, and an
  absent key tells Seedance the same thing for free. Empty strings stay: those
  are answers the model actually gave. Reference files are images, videos
  **and** audio (`generate_video_seedance2` takes all three lists).
  Two job task types share this one router, dispatched by
  `job["task"]`: `"replicate_intake"` (download + LLM analysis, rendered by
  `replicate_review.html`, which plays the source reel via
  `GET /replicate/video/{job_id}` next to the editable prompt JSON) and
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
`dependencies=[Depends(AuthMiddleware.require_admin)]`, so a new endpoint added to these
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
  `settings.web.public_prefixes`) magic-link approval endpoint for `todo.py`'s
  uploaded assets. Read that allowlist before adding anything here.

## At the `routers/` root

- `auth.py` — `/login`, `/logout`.
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
  `write_image_thumb` is the shared Pillow thumbnailer (also used by the
  models router).
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
`static/js/gallery-scroll.js` is the infinite scroll: an
`IntersectionObserver` on `.gallery-sentinel[data-next-offset]` inside any
`[data-gallery-endpoint]` container fetches the next page of cards as a
fragment and swaps the sentinel for it. It calls
`Generation.resumePendingCards()` afterwards (idempotent — `data-resumed`
marks cards already being polled) so an appended still-running card resolves
inline. `wireDownloadButtons` needs no such call: it is one delegated document
listener, so appended cards are covered already.
