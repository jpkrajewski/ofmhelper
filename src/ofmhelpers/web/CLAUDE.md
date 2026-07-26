# Module purpose

The FastAPI web app ("Global Ascend LLC — Content Ops"): a single-password,
two-role (admin/VA) internal tool for AI content generation (Seedance,
Kling, Nano Banana Pro, reel cloning), media downloading/cleaning, scraping,
a VA todo list with Discord/Drive approval handoff, and file management. No
SPA framework — Jinja2 server-rendered templates + a small shared vanilla-JS
layer for background-job polling.

**Read this file before adding a new generation/background-job tool** — the
pattern below (jobs.py + task_helpers.py + one router file) is reused
verbatim by seven+ tools; a new one should almost never need new plumbing.

# Top-level files

- `main.py` — assembles the app: middleware order (AuthMiddleware added
  first so SessionMiddleware ends up outermost — Starlette applies
  middleware outside-in in *added* order), static mount, `lifespan`
  (reloads job history via `jobs.load_jobs()`, starts `recovery.py`'s
  background sweeper), and one `include_router(...)` call per router below.
  **Adding a router = one import line + one `include_router` line here.**
- `auth.py` — single shared password per role (`APP_PASSWORD_ADMIN`/`_VA`
  env vars), one `AuthMiddleware` that gates every request via a signed
  session cookie. `PUBLIC_PATHS`/`PUBLIC_PREFIXES` allowlist unauthenticated
  routes (keep this short — anything not listed is protected by default).
  `get_kie_api_key(request)` pre-fills the kie.ai API key field based on
  role. `require_admin` is a FastAPI dependency for admin-only routers.
- `jobs.py` — **the core background-job pattern every generation/download
  tool uses.** Backed by Postgres via `db/` (was an in-memory `JOBS` dict +
  `uploads/jobs.json`); the public API is unchanged. `create_job(task_name,
  params, actor)` -> `enqueue(run_job, job_id, fn, kwargs)` (see `queue.py`)
  -> `get_job(job_id)` for polling. `run_job` catches exceptions and stores
  just the message (not a traceback) as `job["error"]`. Status transitions
  are atomic single UPDATEs now (the old read-modify-write race is gone).
  `list_jobs()` self-heals history when a result file was deleted on disk.
  Result files still live inside a job's `result` payload (JSONB) — there is
  no separate file-reference table.
- `db/` — the persistence layer (Postgres). `models.py` (SQLAlchemy tables:
  jobs, todos, approval_tokens), `session.py` (lazy engine/session from
  `settings.infra`), `repository.py` (**the only code that touches the DB** —
  jobs/todos/approval_tokens delegate to it), `backfill.py` (one-time JSON ->
  Postgres migration, driven by `scripts/backfill_state.py`),
  `recover_orphaned_jobs.py` (idempotent: inserts a `done` job row for any
  `kieai_out/` file no job's `result` references yet — covers a completion
  write that never landed in Postgres; run every deploy via `deploy.sh`, safe
  to run repeatedly). Schema changes are versioned with Alembic (`alembic/`
  at the repo root).
- `queue.py` — the RQ queue (Redis) the API enqueues onto and the `worker`
  container consumes, replacing FastAPI BackgroundTasks. `enqueue(...)` runs
  jobs on the worker in prod; in the test suite (`OFM_RQ_ASYNC=false`) it runs
  them inline, exactly like the old BackgroundTasks, so TestClient still sees
  results immediately.
- `schemas.py` — Pydantic v2 models (`Job`/`Todo`/`ApprovalToken`), the typed
  contract at the persistence boundary.
- `recovery.py` — background sweeper (every `SWEEP_INTERVAL_S` = 300s)
  calling `KieAIClient.resume_pending()` for every configured kie.ai API
  key, so an in-request poll timeout or a server restart mid-generation
  still gets downloaded automatically.
- `todos.py` — persisted VA task list (model name, link to replicate,
  comments), Postgres-backed via `db/` (public API unchanged). Durable across
  restarts (losing outstanding tasks would be a real problem, unlike losing
  job *history*).
- `approval_tokens.py` — single-use "magic link" tokens (no login needed)
  for approving a VA-uploaded asset, used by `routers/approve.py`. Postgres-
  backed via `db/`. Snapshots the asset path it was issued for; `consume()`
  reports "stale" rather than approving the wrong file if the asset was
  replaced after the link went out.
- `helpers_registry.py` — `HELPERS: list[HelperEntry]`, the registry for the
  `/helpers` index page (radio-comms, elevenlabs, scraper, ...). Add one
  entry here for a new "helper" tool; the index page itself is generic.
- `templates_config.py` — the shared `Jinja2Templates` instance every router
  imports.

# `routers/` — one file per feature

**The shared "upload -> background job -> poll -> download" pattern**
(`task_helpers.py`) is what makes every generation tool below a thin file:
`ASSETS_ROOT` (content-addressed shared upload store, deduped by sha256),
`build_ordered_paths` (reconciles a JSON manifest of new+reused reference
files — never re-uploads a file the client already has), `asset_card` /
`job_status_payload` / `serve_job_file` / `job_inputs` (generic
response-shaping every status/polling/download endpoint reuses verbatim).
`flatten_grouped_results` / `grouped_job_status_payload` is the sibling
pattern for tools whose result is grouped by source URL (the two
download-* tools) rather than one-file-per-job.

**Standard tool router shape** (seedance.py/kling.py/nbp.py/fake_ai.py/
replicate.py all follow this): `POST /<prefix>/run` (or `/intake` +
`/generate` for a two-stage flow like replicate) creates a job and
backgrounds the real work, returning `{"job_id": ...}` immediately; `GET
/<prefix>/jobs/{id}` renders `templates/job_status.html`; `GET
/<prefix>/jobs/{id}/status` returns the JSON polling payload; `GET
/<prefix>/files/{id}/{index}` streams the result file. Adding a new
generation backend = write `_run_<tool>(...)` calling that backend + these
five endpoints wired to `task_helpers`, nothing else.

- `generate.py` — the unified tool-picker page (`/generate`): one form whose
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
  `job_status.html` — its Stage-2 form wires straight into
  `static/js/generation.js` like every other tool).
- `download_reels.py` (prefix `/download-videos`) / `download_images.py` —
  bulk downloaders using `downloaders.generic`/`downloaders.images`, the
  grouped-by-source-URL result shape.
- `download_assets.py` — unified tool-picker page for download-videos/
  download-images/clean-images, same style as `generate.py`.
- `clean_image.py` — strips image metadata (`utils.metadata_cleaner`).
- `scraper.py` — runs the Instagram/TikTok scraper pipeline
  (`config.scrapers`, `scraping.*`), exports/filters spreadsheets.
- `radio_comms.py` — radio-comms audio FX (`utils.radio_comms_fx`).
- `el.py` (prefix `/helpers/elevenlabs`) — ElevenLabs TTS generation.
- `helper_index.py` — renders the `/helpers` index from
  `helpers_registry.HELPERS`.
- `todo.py` — admin-managed VA task list (add/toggle/delete/export/import
  admin-only, VAs see + upload a "ready asset" which triggers a Discord
  notification with a magic-link approval button).
- `approve.py` — public (no-login, outside `AuthMiddleware`'s protected
  paths) magic-link approval endpoint for `todo.py`'s uploaded assets — see
  `approval_tokens.py`.
- `file_manager.py` — admin-only browse/download/delete under
  `uploads/`/`downloads/`.
- `action_log.py` — admin-only audit log of every job across every task type
  (`TASK_STATUS_PREFIX` maps a job's task name to its status-page URL prefix
  — add an entry here for any new task type, same idea as `generate.py`'s
  registry but repo-wide, not just the AI-gen gallery).
- `refs.py` — serves/lists previously-uploaded reference files from
  `ASSETS_ROOT` for the file-picker widget's "reuse" browser.
- `cookies.py` — admin upload endpoint for `cookies/cookies.txt`
  (`downloaders.cookies`).
- `auth.py` (router) — `/login`, `/logout`.

# `templates/` and `static/`

Server-rendered Jinja2, extending `base.html` (brand header + role-aware
nav — add a new top-level page's link in `base.html`'s `nav_items` list).
Shared partials: `_file_picker.html` (multi-file ordered picker macro),
`_kie_api_key_field.html`, `_asset_grid.html`/`_asset_media.html` (result
rendering), `job_status.html` (the generic status/polling page every
standard-shape tool reuses).

`static/js/generation.js` is the shared "submit -> poll -> render inline"
controller: any `<form data-prefix="..." data-result-kind="video|image">`
auto-wires on page load, POSTs to `form.action`, then polls
`${prefix}/jobs/${job_id}/status` and swaps the pending card for the result
(or error) in place — no page navigation. `static/js/file-picker.js` is the
reference-file picker widget (`FilePicker.collectFormData`); works with the
manifest reuse pattern in `task_helpers.build_ordered_paths`.
`static/js/prompt-highlight.js` highlights `[Image1]`/`@audio1`-style
reference markers in a prompt textarea (only relevant to tools that use
that marker convention, e.g. `generate.py`'s unified form).
