# Module purpose

Reel cloning: given a viral reel (link or file), download it, hand the
**video itself** plus one fixed analysis prompt to an LLM, validate the
Seedance 2.0 prompt JSON it returns, let the user edit that JSON in the
browser, and fire the real video generation through the existing kie.ai
integration.

The user's only input is the video. There is no shape/look/gender/persona
picker, no transcript, no frame extraction, no locally-assembled prompt --
those were all inputs to a template prompt builder that no longer exists.
Everything about the clone now comes out of the model's own reading of the
reel, driven by `prompts.load_analysis_prompt()`.

Pipeline order: `intake.py` (download + probe duration) -> `llm/` (one call:
video + prompt -> raw text) -> `models/` (parse/validate that text) ->
user edits the JSON in the browser -> `generation.py` (fires the real kie.ai
Seedance 2 call). `pipeline.analyze()` wires the first three together and is
the only entry point the web layer calls.

# Module files

- `prompts.py` — **every prompt this module sends**, and the only place any of
  them lives: the analysis pass' system instruction
  (`DEFAULT_ANALYSIS_SYSTEM_PROMPT` / `load_analysis_system_prompt()`) and user
  prompt (`DEFAULT_ANALYSIS_PROMPT` / `load_analysis_prompt()`), plus the
  second pass' (`DEFAULT_HUNT_PROMPT` / `load_hunt_prompt()`). A provider in
  `llm/` holds no prompt text — it is an API call, not a copywriter, and
  tuning a prompt must never mean editing a client.
  Each is overridable by its own file under the bind-mounted `uploads/` dir
  (`prompt_file` / `system_prompt_file` / `hunt_prompt_file`, i.e.
  `REEL_MACHINE_PROMPT_FILE` / `_SYSTEM_PROMPT_FILE` / `_HUNT_PROMPT_FILE`),
  read per call so an edit on the server lands on the next job — no rebuild,
  no restart. An absent or empty file falls back to the constant.
  Substitution into an override is `str.replace` on `{{ANALYSIS}}` /
  `{{MAX_ITEMS}}` markers, never `str.format`: these files are hand-edited on
  a server and the analysis prompt is already full of literal `{`.
  The analysis prompt is sent with every reel verbatim. It asks for a fixed JSON
  object and says, in its own text, that the main subject's physical
  appearance must NOT be described (identity comes from the user's reference
  images at generation time); that constraint lives in the prompt now, not
  in surrounding code. It also insists the model **break the clip down**
  rather than summarize it (one `scene_events` entry per moment, one `shots`
  entry per continuous camera behavior, covering the clip with no gaps) —
  a weaker model otherwise answers with a single shot spanning the whole
  video and a `scene_event_cue` holding the entire dialogue.
  The pipeline calls the `load_*` functions, never the constants.
  `load_analysis_prompt(context)` appends the operator's per-reel note (the
  `/replicate` form's Context field) after `CONTEXT_HEADER`, at the very
  **end**: the prompt finishes with the JSON template's closing brace, so
  anything spliced in earlier reads as part of the shape being asked for. An
  empty context leaves the prompt byte-identical.
- `models/` — the module's data models, one file per concern: `analysis.py`
  (`ReelAnalysis` + `Person`/`SceneEvent`/`Shot`, plus the parsing that owns
  them: `ReelAnalysis.strip_code_fence` and `ReelAnalysis.from_llm_text(text)
  -> ReelAnalysis`), `errors.py` (`AnalysisError`), `hunt.py` (`HuntIdeas`,
  including the slug/dedupe cleaning of a second-pass answer via
  `HuntIdeas.from_llm_json`). Import from the package. `REQUIRED_KEYS` only covers `ReelAnalysis`' own fields, so the
  nested sections need their own prompt/schema drift test (see
  `test_prompt_asks_for_every_scene_event_key_too`) — `extra="forbid"` turns a
  key the prompt stopped asking for into a rejected real answer.
  Nullable-vs-required tracks what the prompt says: `SceneEvent.action` is
  required ("always fill this in even if no dialogue"), while `line`,
  `delivery`, `pose` and `facial_expression` are `| None` because the prompt
  asks for null on a silent moment or on anyone off camera. Those nulls are a
  signal to the analysis model, not something Seedance needs — the web layer
  strips them before generating (`_drop_nulls` in
  `web/routers/generation/replicate.py`).
  The only gate between raw model output and a prompt we
  treat as real: strips a ``` fence / surrounding prose the prompt asked the
  model not to add, then validates against `ReelAnalysis`. Validation is
  **strict** in both directions: `strict=True` (no `"3"` -> `3` coercion)
  and `extra="forbid"` (a key `ANALYSIS_PROMPT` never asked for means the
  model went off-script). A failure raises `AnalysisError`
  carrying `.raw`, the provider's untouched answer: the caller shows that
  instead of failing the job (see `pipeline.py`).
  `ReelAnalysis.elevenlabs_ready_prompt_from_subject(speaker="subject")`
  returns just that person's dialogue in ElevenLabs' bracket-cue form —
  `[delivery] line [pause] [delivery] next line` — so the same analysis
  feeds `/helpers/elevenlabs` without re-typing the script. Silent
  `scene_events` (the ones with `line: null`) contribute the pause, not
  text.
- `intake.py` — `fetch_source` (local file or yt-dlp download, reuses
  `downloaders.generic.download`; a failed Instagram download gets a hint
  appended by `_instagram_hint()`, which branches on `get_cookiefile()`
  because the two cases have opposite fixes: **no** cookies means the request
  was logged out and needs an upload at `/cookies` (see
  `web/routers/admin/cookies.py`), while cookies **present** means Instagram
  refused a logged-in request — that burner's session is expired,
  rate-limited or flagged, and re-uploading the same file changes nothing),
  `probe_duration` (ffprobe
  subprocess -- the source reel's own length, so the clone always matches it
  exactly; no manual "Length" field anywhere in the web layer), and
  `run_intake` -> `IntakeResult(video_path, duration, source_url)`. The old
  `extract_frames`/`transcribe` (faster-whisper)/`diarize` (pyannote) steps
  are gone: the model watches the video, so a separate transcript and frame
  sequence bought nothing. `build_contact_sheet` went the same way with the
  Anthropic provider it existed for, so nothing shells out to ffmpeg here
  any more — only ffprobe.
- `hunt.py` — the **second pass**: `suggest_hunt(analysis) ->
  HuntIdeas(instagram_topics, search_queries, outfit_ideas)`. Gemini describes
  the reel in prose, and prose makes bad search terms ("the D Las Vegas, a
  hotel and casino located on the Fremont Street Experience" is not what
  anyone types into Instagram), so the finished analysis goes to a text model
  (`llm/registry.get_text_provider()`, Groq by default) which turns it into
  Instagram topic slugs, search phrases and alternative outfits.
  The HTTP call belongs to the provider and the prompt to `prompts.py`; what
  is left here is this module's own share: which parts of the analysis are
  worth sending, and what a usable answer looks like coming back. Only the
  "what is this" fields go (`_analysis_digest`) — `scene_events`/`shots`
  describe how to *film* it and say nothing about what to search for — and
  only the **main subject's** wardrobe (`ReelAnalysis.subject`): the other
  `people` entries are the cameraman and whoever walked past, whose "wardrobe:
  not visible" produced outfit ideas for nobody.
  **Best-effort by construction**: no provider configured, an HTTP error, a
  non-JSON answer or a wrong-shaped one all return empty lists, never raise
  (`get_text_provider()` answers `None` rather than raising on a missing key,
  which is the one place this module differs from the video pass). It runs after the
  download and the analysis, so failing the job here would throw away real
  work for a nice-to-have, and the web layer falls back to terms derived
  mechanically from the analysis. Topic slugs are normalized on the way out
  (`#Starbucks Girl!` -> `starbucks-girl`): they address
  `instagram.com/popular/<slug>`, the one Instagram surface that still works
  logged out — its keyword search and `/explore/tags/` pages do not.
- `pipeline.py` — `analyze(source, work_dir, llm_provider=None) ->
  AnalysisResult(video_path, duration, provider, raw, prompt, error)`, the
  entry point `web/routers/generation/replicate.py` calls: `run_intake` ->
  `provider.analyze_video(video, load_analysis_prompt(context),
  system_prompt=load_analysis_system_prompt())` -> `ReelAnalysis.from_llm_text` ->
  `suggest_hunt`.
  Everything before validation still raises (a failed download or a missing
  API key leaves nothing to show), but **validation itself never fails the
  job**: a response that doesn't match `ReelAnalysis` comes back with
  `prompt=None`, `error` set, and `prompt_text` equal to the provider's raw
  answer, so the review page can show it and let the VA fix it by hand.
  `duration` is the probed source length clamped into Seedance's supported
  4-15s range (`clamp_duration`). `AnalysisResult.speech` is the subject's
  ElevenLabs-ready dialogue (empty when validation failed — there is no
  typed dialogue to read).
- `generation.py` — `generate_reel_clone(...)`: uploads the character
  reference images (plus optional reference videos/audio — Seedance 2 takes all
  three lists) and calls the existing
  `KieAIClient.generate_video_seedance2` (no separate HTTP client — reuses
  `aigenproviders/kaiai/client.py`). Empty reference lists are passed as `None`,
  not `[]`: the client only puts a `reference_*_urls` key in the payload for the
  lists that are set. Defaults to `aspect_ratio="9:16"` (reels are vertical,
  unlike `web/routers/generation/seedance.py`'s `16:9` default).

## `llm/` — the providers

Two capabilities, one method each: `analyze_video(video_path, prompt, *,
system_prompt="") -> str` for the video pass and `complete_json(prompt) ->
str` for the text one. A provider returns raw text and holds no prompt of its
own; parsing/validating is the caller's job (`models/analysis.py` for pass 1,
`hunt.py` for pass 2), so a provider stays a thin API call and the "is this
usable" rule lives in exactly one place per pass.

Two Protocols rather than one because the two passes are not the same
capability: pass 1 needs an API that takes video, pass 2 only needs chat
completions — which is exactly why the cheap second pass can run on a
different vendor.

- `__init__.py` — the `LLMProvider` and `TextLLMProvider` Protocols, right
  where `from ofmhelpers.reel_machine.llm import LLMProvider` finds them.
- `gemini_provider.py` — `GeminiProvider` (`name = "gemini"`), **the only
  video provider**. Google's Gemini API is the only free tier that takes real
  VIDEO input, which is the whole point: motion, timing, transitions, and
  audio are exactly what the per-second `scene_events`/`shots` sections
  need. Uploads via `client.files.upload` and polls for `state == "ACTIVE"`
  before the `generate_content` call (`_VIDEO_ACTIVE_TIMEOUT_S`); a failed
  or slow upload raises rather than silently downgrading to stills.
  The generate call is retried with a widening gap while Gemini answers with
  a transient status (`_RETRY_STATUS`, the 5xx family — the free tier's "high
  demand ... usually temporary" 503 is the common one), twice, ~6s worst case
  because this runs inside an RQ worker slot. Only `generate_content` is
  wrapped, not the upload: the file is already ACTIVE, so a retry re-asks
  about it instead of re-sending the video, and failing there used to throw
  away the download the intake had just paid for.
  **429 is deliberately not retried**: on the free tier it is normally the
  daily quota, which no amount of sleeping clears — it fails fast and the VA
  reruns the intake later via `/replicate?from=<job_id>`, which reuses the
  reel already on disk. Anything else (bad key, rejected schema) also fails
  on the first try.
  Both prompts come in from `prompts.py` via `pipeline.analyze` —
  `system_prompt` becomes `system_instruction`. `temperature=0.15` is
  load-bearing, not a default: this is transcription of what is on screen,
  and sampling variety is what makes the model paraphrase four camera beats
  into one summary shot.
  Constrains decoding to `models.ReelAnalysis` via
  **`response_json_schema`** — not `response_schema`, whose OpenAPI subset
  400s on the `additionalProperties: false` that `extra="forbid"` emits — so
  the model physically cannot return a fence, prose, a missing key or an
  invented one. `ReelAnalysis.from_llm_text` still runs on the result: a schema Google
  rejects, or a future provider without constrained decoding, must not
  quietly degrade into an unvalidated free-text answer. Defaults to the
  `gemini-flash-latest` ALIAS, not a pinned dated id -- Google documents the
  alias as always pointing at their current flash release, specifically so
  this doesn't break again the way pinning `gemini-2.0-flash` did when they
  retired it (free-tier calls started 429ing with a `limit: 0` quota instead
  of a normal "used it all up" error). `GEMINI_API_KEY` from
  https://aistudio.google.com/apikey; `GEMINI_MODEL` overrides the model.
  Unrelated to `gdrive/` — that's OAuth-as-a-user Drive access, this is a
  plain API-key call.
- `groq_provider.py` — `GroqProvider` (`name = "groq"`), the **text**
  provider the second pass uses. A separate vendor from the video pass on
  purpose: pass 2 runs on every intake, and putting it on Gemini would spend
  the one quota that actually matters — the free tier that takes video — on
  the nice-to-have. Groq's API is OpenAI-shaped
  (`/openai/v1/chat/completions`), so this is one plain `requests` POST with
  JSON mode on, no SDK, in keeping with `aigenproviders/kaiai`. No retry: the
  caller treats the whole pass as best-effort and has its own fallback, so a
  busy model costs a worker nothing to give up on. `GROQ_API_KEY` from
  https://console.groq.com (free, no card); `GROQ_MODEL` overrides the model.
- `registry.py` — `PROVIDERS` / `TEXT_PROVIDERS`, `{name: factory}` maps
  keyed off each provider's own `name`, plus `get_provider(name=None)`
  (explicit name, else `REEL_MACHINE_LLM_PROVIDER`, else `"gemini"`) and
  `get_text_provider(name=None)` (else `REEL_MACHINE_TEXT_PROVIDER`, else
  `"groq"`). **No fallback provider**: an unknown name raises, so a typo in
  the deployment can't quietly run a model nobody chose. A missing *key*
  raises too for the video pass ("GEMINI_API_KEY" fails the job) but makes
  `get_text_provider` answer **`None`** — pass 2 is optional by design, and
  that is the difference between "the job can't run" and "the review page
  falls back to derived search terms". Adding a provider is one entry in a
  map.

There was a second provider (Anthropic, contact-sheet based) and it is
gone: Claude's Messages API vision is image-only, and a tiled grid of 1-fps
frames has no motion, no timing and no audio — all of which the prompt asks
about. It was strictly worse at the one job this module does.

# Dependencies

`google-genai` (Gemini) is a base dependency and free — imported at module
top, since it's never optional. `ffprobe` must be on PATH; ffmpeg is no
longer used here at all.

# Who calls this

`web/routers/generation/replicate.py` is the only caller — see its own
module docstring and `web/CLAUDE.md` for how the two-stage job flow (intake/
analyze -> review/edit -> generate) is wired to this package. Both stages
are ordinary jobs, so they appear in the Action log like every other tool.
