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
video + prompt -> raw text) -> `schema.py` (parse/validate that text) ->
user edits the JSON in the browser -> `generation.py` (fires the real kie.ai
Seedance 2 call). `pipeline.analyze()` wires the first three together and is
the only entry point the web layer calls.

# Module files

- `prompts.py` — `DEFAULT_ANALYSIS_PROMPT` + `load_analysis_prompt()`, the
  single prompt sent with every reel, verbatim. It asks for a fixed JSON
  object and says, in its own text, that the main subject's physical
  appearance must NOT be described (identity comes from the user's reference
  images at generation time); that constraint lives in the prompt now, not
  in surrounding code. It also insists the model **break the clip down**
  rather than summarize it (one `scene_events` entry per moment, one `shots`
  entry per continuous camera behavior, covering the clip with no gaps) —
  a weaker model otherwise answers with a single shot spanning the whole
  video and a `scene_event_cue` holding the entire dialogue.
  The pipeline calls `load_analysis_prompt()`, not the constant: whatever is
  in `settings.reel_machine.prompt_file` (`REEL_MACHINE_PROMPT_FILE`,
  default `uploads/analysis_prompt.txt`) wins, and `uploads/` is
  bind-mounted into both the API and the worker, so the prompt is retunable
  on the server with an editor — no rebuild, no restart, next job picks it
  up. An absent or empty file falls back to the constant.
- `schema.py` — the Pydantic models (`ReelAnalysis` + `Person`/`SceneEvent`/
  `Shot`), `AnalysisError`, `strip_code_fence`, `parse_analysis(text) ->
  ReelAnalysis`. The only gate between raw model output and a prompt we
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
  appended pointing at `/cookies` -- Instagram blocks most logged-out reel
  downloads, and this repo already has cookie-upload support for exactly
  that, see `web/routers/admin/cookies.py`), `probe_duration` (ffprobe
  subprocess -- the source reel's own length, so the clone always matches it
  exactly; no manual "Length" field anywhere in the web layer), and
  `run_intake` -> `IntakeResult(video_path, duration, source_url)`. The old
  `extract_frames`/`transcribe` (faster-whisper)/`diarize` (pyannote) steps
  are gone: the model watches the video, so a separate transcript and frame
  sequence bought nothing. `build_contact_sheet` went the same way with the
  Anthropic provider it existed for, so nothing shells out to ffmpeg here
  any more — only ffprobe.
- `pipeline.py` — `analyze(source, work_dir, llm_provider=None) ->
  AnalysisResult(video_path, duration, provider, raw, prompt, error)`, the
  entry point `web/routers/generation/replicate.py` calls: `run_intake` ->
  `provider.analyze_video(video, load_analysis_prompt())` -> `parse_analysis`.
  Everything before validation still raises (a failed download or a missing
  API key leaves nothing to show), but **validation itself never fails the
  job**: a response that doesn't match `ReelAnalysis` comes back with
  `prompt=None`, `error` set, and `prompt_text` equal to the provider's raw
  answer, so the review page can show it and let the VA fix it by hand.
  `duration` is the probed source length clamped into Seedance's supported
  4-15s range (`clamp_duration`). `AnalysisResult.speech` is the subject's
  ElevenLabs-ready dialogue (empty when validation failed — there is no
  typed dialogue to read).
- `generation.py` — `generate_reel_clone(...)`: uploads character reference
  images and calls the existing `KieAIClient.generate_video_seedance2` (no
  separate HTTP client — reuses `aigenproviders/kaiai/client.py`). Defaults
  to `aspect_ratio="9:16"` (reels are vertical, unlike
  `web/routers/generation/seedance.py`'s `16:9` default).

## `llm/` — the provider

One capability, one method: `analyze_video(video_path, prompt) -> str`. The
provider returns raw text; parsing and validating it is `schema.py`'s job,
so the provider stays a thin API call and the "is this usable" rule lives in
exactly one place.

- `__init__.py` — the `LLMProvider` Protocol, right where `from
  ofmhelpers.reel_machine.llm import LLMProvider` finds it.
- `gemini_provider.py` — `GeminiProvider` (`name = "gemini"`), **the only
  provider**. Google's Gemini API is the only free tier that takes real
  VIDEO input, which is the whole point: motion, timing, transitions, and
  audio are exactly what the per-second `scene_events`/`shots` sections
  need. Uploads via `client.files.upload` and polls for `state == "ACTIVE"`
  before the `generate_content` call (`_VIDEO_ACTIVE_TIMEOUT_S`); a failed
  or slow upload raises rather than silently downgrading to stills.
  Constrains decoding to `schema.ReelAnalysis` via
  **`response_json_schema`** — not `response_schema`, whose OpenAPI subset
  400s on the `additionalProperties: false` that `extra="forbid"` emits — so
  the model physically cannot return a fence, prose, a missing key or an
  invented one. `parse_analysis` still runs on the result: a schema Google
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
- `registry.py` — `PROVIDERS`, a `{name: factory}` map keyed off each
  provider's own `name`, and `get_provider(name=None)`: explicit name, else
  `REEL_MACHINE_LLM_PROVIDER`, else `"gemini"`. **No fallback provider**: an
  unknown name or a missing key raises, so the job fails with
  "GEMINI_API_KEY" rather than quietly running a model nobody chose. Adding
  a provider is one entry in the map.

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
