# Module purpose

External AI-generation API clients. One subpackage per provider aggregator.
Currently only one provider is integrated: kie.ai (a Market API that proxies
several underlying models — Seedance, Kling, Nano Banana Pro — behind one
task-queue interface).

Convention for adding a new provider: `aigenproviders/<provider_name>/client.py`,
one class named `<Provider>Client`, a `.from_env(...)` classmethod for
constructing it from environment variables, plain dicts for request/response
payloads (no pydantic models here currently, even though pydantic is a project
dependency).

# Module files

- `kaiai/client.py` — `KieAIClient`. Sync (`requests`-based) wrapper around
  the kie.ai Market API (`https://api.kie.ai/api/v1/jobs`). Handles the full
  async task lifecycle: `create_task` -> `poll_task`/`check_task` ->
  `download_urls`, plus one wrapper per model (`generate_image_nbp`,
  `generate_image_seedream45`, `generate_image_seedream5`,
  `generate_image_gpt25_flare`, `generate_video_seedance2`,
  `generate_video_seedance25`, `generate_video_kling3`, `generate_video_wan3`,
  `generate_video_minimax_h3`). A wrapper only builds its payload; the shared
  create -> poll -> preview -> download tail is `_generate`. Plus crash/timeout recovery (`resume_pending`, used by `web/recovery.py`'s
  background sweeper). Also handles uploading local reference files
  (`upload_local_file`), memoized in Redis (see below). Note: the directory is
  named `kaiai` (typo, kept for backwards compatibility with existing
  imports) — everything else (docs, env vars, tests) says "kie"/"kie.ai".
- `kaiai/types.py` — every kie.ai model id (`KieModel`), the video-model set
  recovery uses to name downloads, and one `StrEnum` per per-model value set
  (resolutions, modes, qualities, variants). Nothing else spells a model id or
  an allowed value; wrappers validate by constructing the enum, routers type
  their form fields with it.
- Upload memoization has no module of its own: `upload_local_file` reads and
  writes `kieai:upload:<api_key>:<path>` through `ofmhelpers.cache`, so the
  same local reference file isn't re-uploaded to kie.ai on every generation.
  Keyed by API key as well as path because kie.ai namespaces uploads per
  account. In Redis rather than in-process so the API and the worker share one
  answer; TTL is `OFM_KIEAI_UPLOAD_CACHE_TTL_S`, and every hit is confirmed
  live with a HEAD before it is trusted. Pure optimisation — a dead broker
  just means the file is uploaded again.

# Who calls this

The kie.ai modules in `web/routers/generation/` (`seedance.py`,
`seedance25.py`, `kling.py`, `wan.py`, `minimax.py`, `nbp.py`, `seedream45.py`,
`seedream5.py`, `gpt_image.py`), `fake_ai.py`, and
`reel_machine/generation.py` all construct a `KieAIClient.from_env(api_key=...)`
and call one of its `generate_*` methods. `web/recovery.py`'s background
sweeper calls `resume_pending()` every few minutes across every configured API
key. Never build a second HTTP client for a model kie.ai already exposes —
add a wrapper method to `KieAIClient` instead.
