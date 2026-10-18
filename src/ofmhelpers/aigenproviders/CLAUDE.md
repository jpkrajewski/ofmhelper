# Module purpose

AI generation providers behind one interface, and every model they offer as
data. The web app (`web/routers/generation/ai.py`, `clone.py`,
`web/recovery.py`) never names a provider or a model field: it reads the
catalog and calls the interface.

Providers today: **kie.ai** (`kaiai/`), **Higgsfield** (`higgsfield/`), and
**fake** (`fake/`, calls nothing; for testing the pages for free).

# The shared layer

- `base.py` — `Provider`, the interface: `upload(path) -> url`,
  `submit(spec, payload, idempotency_key) -> task id`,
  `status(task id) -> TaskState` (pending / done + urls / failed + error),
  `download(url, dest)` (plain HTTP by default), `default_key(admin)` (the
  per-role key the form pre-fills), class vars `name`, `label`, `rank`
  (place in the provider select), `needs_key`. `catalog_path()` is the
  `catalog.json` next to the provider's module. Providers register with the
  `@register_provider` class decorator into an `lru_cache`d registry;
  `provider_classes()` imports every subpackage's `provider` module once, so
  registration happens on first use. Lookups: `provider_class(name)`,
  `get_provider(name, key)`, `provider_names()`.
- `spec.py` — `ModelSpec`, one catalog entry: `provider`, `model_id`,
  `label`, `family`, `kind` (image / video), the model's raw JSON
  `input_schema`, `fixed` (sent on every request, never shown: kie.ai's
  `nsfw_checker: false`), `default_prompt`. `key` is
  `"<provider>:<model id>"` — what the form and a job's params name it by.
  From the schema it derives `media_fields` (file pickers: a `format: uri`
  string or array, or a `*_url(s)` name, kind from the name — video / audio
  / image / frame — or our own `x-media` keyword when the name says nothing,
  e.g. kie's `image_input`), `form_fields` (scalars only; arrays/objects and
  URL fields of no media kind are left to the provider's default),
  `parse_form` (strings -> typed values via a `create_model` Pydantic model;
  blank = omitted), `check` (`jsonschema` Draft 2020-12 against the raw
  schema, so allOf/if/then/additionalProperties apply too — run before
  anything is uploaded or paid for) and `request` (fixed + values, media
  paths uploaded through the provider; a single-file field sends one URL,
  an empty list is left out). `FieldSchema` models only the JSON-schema
  subset the form needs and unwraps `anyOf: [X, null]`.
- `catalog.py` — `catalog()` (every provider's `catalog.json`, loaded once,
  validated into `ModelSpec`s), `get_spec(key)`, `models_for(provider, kind)`.
- `runner.py` — `generate(provider, spec, payload, job_id, on_result_urls)`,
  the one request lifecycle: resume the job's remembered request or submit
  (and remember) a new one, poll `status` with backoff
  (`settings.aigen.poll_*`, video waits longer), retry retryable status
  errors, raise `ProviderGenerationError` on failure, hand hosted URLs to the
  hook before downloading every result into `settings.aigen.out_dir` as
  `<provider>_<task id>[_<i>].<ext>`. Past the deadline it raises
  `ProviderTimeoutError` and leaves the request remembered for recovery.
- `tasks.py` — `ProviderTask` per job in Redis (`aigen:task:<job id>`, TTL
  `resume_max_age_s`): provider, model, task id and the **key** it was made
  with (a pasted key is in no env var; the key is why this is not on the job
  row, whose params are shown on its page). `remember` / `recall` /
  `forget` / `remembered`. Redis errors are logged, never raised — losing
  only resumability beats failing a paid generation.
- `errors.py` — `InvalidInputError` (our schema check) and the
  `ProviderError` family: `ProviderAuthError`, `ProviderInputError`,
  `ProviderGenerationError`, `ProviderUnavailableError` and
  `ProviderTimeoutError` (both `retryable`). Adapters map their own codes
  onto these; nothing downstream parses a provider's text.

# Adding a provider (fal.ai, Replicate, ...)

1. `aigenproviders/<name>/__init__.py`, `provider.py` with one
   `@register_provider class XProvider(Provider)`, `models.py` with
   Pydantic models of its wire format (`extra="ignore"`), `client.py` only if
   there is no official SDK.
2. `<name>/catalog.json`: a list of `{model_id, label, family, kind,
   input_schema[, fixed, default_prompt]}`. If the provider publishes
   schemas, generate it with a script like
   `scripts/sync_higgsfield_catalog.py`.
3. Map every failure onto `errors.py` and every status onto `TaskState`.
4. Tests with its HTTP faked at the wire, like `tests/aigenproviders/`.

Nothing else changes: /generate lists it, `/ai/run` runs it, recovery
recovers it (`tests/aigenproviders/test_catalog.py` and `tests/web/test_ai.py`
prove this with a provider defined in the test). To make it a /clone
provider, add one `CloneTarget` to `web/routers/generation/clone.py`.

# kie.ai (`kaiai/`)

The directory is named `kaiai` (a typo kept for import compatibility).

- `client.py` — `KieAIClient`, single calls only: `upload_local_file`,
  `create_task(model, payload, job_id)`, `get_task(task id)`. All HTTP goes
  through one `requests.Session` with `urllib3.Retry` on connection errors
  and 429/5xx for **GET/HEAD only** — a createTask that reached kie.ai and
  timed out must never be sent twice (it would be paid twice). An HTTP error
  or a non-200 in-body `code` becomes the shared error type
  (`models.raise_for`: 401/403 auth, 408/429/455/5xx unavailable, anything
  else input — 402 is "no credits", 422 bad input). Audit logs: every task
  created goes to `tasks.jsonl` (with its job id), every success's credits
  to `completions.jsonl`.
  Upload memoization: `kieai:upload:<api_key>:<path>` in Redis via
  `ofmhelpers.cache`, keyed by key as well as path because kie.ai namespaces
  uploads per account; every hit is confirmed live with a HEAD first. An
  image reference over `OFM_KIEAI_REF_IMAGE_MAX_BYTES` / `_MAX_SIDE` is
  uploaded as a shrunk JPEG copy (`_fit_for_upload`, `utils/image_fit.py`)
  under `OUT_DIR/fitted/`; the original is never modified.
- `models.py` — kie.ai's envelope (`code`, `msg`), createTask, recordInfo
  (`state` waiting/queuing/generating/success/fail, `resultJson`'s
  `resultUrls`, `failMsg`, `creditsConsumed`) and upload responses.
- `provider.py` — `KieProvider`; the keys are `KIE_AI_API_KEY_ADMIN` /
  `_VA` (`settings.web`). kie.ai takes no idempotency key; a re-run resumes
  instead.
- `catalog.json` — the 17 kie.ai models /generate offered before /ai, one
  entry per kie model id. What the old per-model wrappers hard-coded is data
  here: Seedance's `web_search`/`return_last_frame`/`nsfw_checker` false
  (`fixed`), Nano Banana's always-sent `image_input: []`, Seedream 5.0
  Lite/Pro × text/image as four entries (ultra quality on Lite only),
  MiniMax H3's three input modes as three entries (text-to-video takes no
  `adaptive`), GPT Image's required input images (`minItems: 1`). Kling
  3.0 and Nano Banana carry the form's default prompts.

# Higgsfield (`higgsfield/`)

On the official SDK `higgsfield-client` (0.2.0, httpx), public calls only:
`SyncClient(api_key=...)`, `upload_file`, `submit`, and a request
controller's `status` / `get`.

- `provider.py` — `HiggsfieldProvider`. Keys are one `KEY_ID:KEY_SECRET`
  string (`HF_API_KEY_ADMIN` / `_VA`). `_mapped_errors` turns the SDK's
  single `HiggsfieldClientError` (its `__cause__` carries the HTTP status)
  and httpx transport errors into the shared types. `status` checks the
  status and, once ended, `get`s the full body (outputs or error).
  Uploads are memoized like kie.ai's, keyed by a hash of the key (the key
  itself never lands in Redis), and only the documented types are sent:
  JPEG/PNG/WebP/GIF, WAV, MP4 — anything else (a .mov, an .mp3) is
  `ProviderInputError` before any call.
- `models.py` — the status body: `status`, `error`, `images[].url`,
  `video.url`, `audio.url`.
- `catalog.json` — **generated**, 83 models (67 video, 16 image) from
  `scripts/sync_higgsfield_catalog.py`. Re-run it when Higgsfield ships
  models; it rewrites the file from the docs.

Findings (2026-10-18):

- Base `https://api.higgsfield.ai`; auth header
  `Authorization: Key <KEY_ID>:<KEY_SECRET>`; submit is `POST /<endpoint
  id>` with the model's JSON, answered `{status, request_id, status_url,
  cancel_url}`.
  https://docs.higgsfield.ai/docs (index), https://docs.higgsfield.ai/docs/quickstart.md
- Statuses: `queued`, `in_progress` (in flight), `completed`, `failed` (may
  carry `error`), `nsfw`, `canceled` (terminal). Outputs live at least 7
  days. https://docs.higgsfield.ai/docs/concepts/requests.md
- Uploads: `POST /files/generate-upload-url {content_type}` ->
  `{public_url, upload_url, upload_headers}`, then PUT the bytes to
  `upload_url` with those headers and **no** API key; the presigned URL
  lasts 1 h. https://docs.higgsfield.ai/docs/concepts/file-uploads.md
- Every model page (`docs/models/<family>/<workflow>.md`) carries
  `**Endpoint ID:**` and a "Complete JSON schema" block; families are listed
  on `docs/models/video-generation.md` / `image-generation.md`. Soul ID is a
  training workflow with no endpoint and is skipped. The supplementary
  OpenAPI file (`docs/openapi.json`) lists only 5 models — not a source.
- The SDK retries 408/429/500/502/503/504 three times with backoff, **a
  submit included**, and its `submit` sends no `Idempotency-Key` (the docs
  recommend one). So a submit that reached Higgsfield and then failed can
  in rare cases be charged twice; a job never resubmits once a request id
  is remembered. https://github.com/higgsfield-ai/higgsfield-client ,
  https://docs.higgsfield.ai/docs/how-to/sdk.md

# Who calls this

`web/routers/generation/ai.py` (`/generate`'s form and `/ai/run`; its
`run_ai` is the worker side), `clone.py` (one `run_ai` per video),
`web/recovery.py` (settles remembered requests), `web/api_keys.py` (key
pre-fills) and `web/schemas/generation.py` (the provider and model lists a
page shows).
