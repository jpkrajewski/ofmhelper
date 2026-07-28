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
reel, driven by `prompts.ANALYSIS_PROMPT`.

Pipeline order: `intake.py` (download + probe duration) -> `llm/` (one call:
video + prompt -> raw text) -> `schema.py` (parse/validate that text) ->
user edits the JSON in the browser -> `generation.py` (fires the real kie.ai
Seedance 2 call). `pipeline.analyze()` wires the first three together and is
the only entry point the web layer calls.

# Module files

- `prompts.py` — `ANALYSIS_PROMPT`, the single frozen prompt sent with every
  reel, verbatim. It asks for a fixed JSON object and says, in its own text,
  that the main subject's physical appearance must NOT be described
  (identity comes from the user's reference images at generation time);
  that constraint lives in the prompt now, not in surrounding code.
- `schema.py` — `REQUIRED_KEYS`, `AnalysisError`, `strip_code_fence`,
  `parse_analysis(text) -> dict`. The only gate between raw model output and
  a prompt we treat as real: strips a ``` fence / surrounding prose the
  prompt asked the model not to add, then rejects anything that isn't a JSON
  object carrying every key `ANALYSIS_PROMPT` asked for (and a list where a
  list was asked for). Raising here fails the job with a readable message
  instead of handing a VA a half-empty prompt to fire -- Seedance is given
  the whole object, so a dropped `scene_events` produces a very different
  video rather than a slightly worse one.
- `intake.py` — `fetch_source` (local file or yt-dlp download, reuses
  `downloaders.generic.download`; a failed Instagram download gets a hint
  appended pointing at `/cookies` -- Instagram blocks most logged-out reel
  downloads, and this repo already has cookie-upload support for exactly
  that, see `web/routers/admin/cookies.py`), `probe_duration` (ffprobe
  subprocess -- the source reel's own length, so the clone always matches it
  exactly; no manual "Length" field anywhere in the web layer),
  `build_contact_sheet` (ffmpeg 4x4 tile, used **only** by the Anthropic
  provider -- see below), and `run_intake` -> `IntakeResult(video_path,
  duration, source_url)`. The old `extract_frames`/`transcribe`
  (faster-whisper)/`diarize` (pyannote) steps are gone: the model watches
  the video, so a separate transcript and frame sequence bought nothing.
- `pipeline.py` — `analyze(source, work_dir, llm_provider=None) ->
  AnalysisResult(video_path, duration, prompt, provider)`, the entry point
  `web/routers/generation/replicate.py` calls. Three steps, no branches and
  no fallbacks: `run_intake` -> `provider.analyze_video(video,
  ANALYSIS_PROMPT)` -> `parse_analysis`. Any failure raises and the job
  records the message; there is nothing to degrade to, because a prompt the
  model didn't actually produce is worse than no prompt. `duration` is the
  probed source length clamped into Seedance's supported 4-15s range
  (`clamp_duration`). `AnalysisResult.prompt_text` is the validated object
  pretty-printed -- what the review textarea shows and what Seedance is
  given.
- `generation.py` — `generate_reel_clone(...)`: uploads character reference
  images and calls the existing `KieAIClient.generate_video_seedance2` (no
  separate HTTP client — reuses `aigenproviders/kaiai/client.py`). Defaults
  to `aspect_ratio="9:16"` (reels are vertical, unlike
  `web/routers/generation/seedance.py`'s `16:9` default).

## `llm/` — the providers

One capability, one method: `analyze_video(video_path, prompt) -> str`.
Providers return raw text; parsing and validating it is `schema.py`'s job,
so each provider stays a thin API call and the "is this usable" rule lives
in exactly one place.

- `base.py` — the `LLMProvider` Protocol.
- `gemini_provider.py` — `GeminiProvider` (`name = "gemini"`), **the
  default**. Google's Gemini API is the only free tier that takes real
  VIDEO input, which is the whole point: motion, timing, transitions, and
  audio are exactly what the per-second `scene_events`/`shots` sections
  need. Uploads via `client.files.upload` and polls for `state == "ACTIVE"`
  before the `generate_content` call (`_VIDEO_ACTIVE_TIMEOUT_S`); a failed
  or slow upload raises rather than silently downgrading to stills.
  Requests `response_mime_type="application/json"`. Defaults to the
  `gemini-flash-latest` ALIAS, not a pinned dated id -- Google documents the
  alias as always pointing at their current flash release, specifically so
  this doesn't break again the way pinning `gemini-2.0-flash` did when they
  retired it (free-tier calls started 429ing with a `limit: 0` quota instead
  of a normal "used it all up" error). `GEMINI_API_KEY` from
  https://aistudio.google.com/apikey; `GEMINI_MODEL` overrides the model.
  Unrelated to `gdrive/` — that's OAuth-as-a-user Drive access, this is a
  plain API-key call.
- `anthropic_provider.py` — `AnthropicProvider` (`name = "anthropic"`), the
  paid opt-in. Claude's Messages API vision is **image-only** (no native
  video input), so this renders the reel down to a contact sheet
  (`intake.build_contact_sheet`) and tells the model what the grid is. It is
  therefore strictly weaker here than Gemini -- a tiled grid of 1-fps frames
  has no motion, no timing, no audio, all of which the prompt asks about.
  Billed per token separately from a claude.ai subscription; needs
  `ANTHROPIC_API_KEY` and `pip install 'ofmhelpers[llm-anthropic]'`.
- `registry.py` — `get_provider(name=None)`: explicit name, else
  `REEL_MACHINE_LLM_PROVIDER`, else `"gemini"`. **No fallback provider**: an
  unknown name or a missing key raises, so the job fails with
  "GEMINI_API_KEY" rather than quietly running a model nobody chose.

# Dependencies

`google-genai` (Gemini) is a base dependency and free. `anthropic` is the
one optional extra (`[project.optional-dependencies] llm-anthropic`) —
never installed unless explicitly requested, since it's paid. `ffmpeg`/
`ffprobe` must be on PATH (`ffprobe` for every job, `ffmpeg` only for the
Anthropic provider's contact sheet).

# Who calls this

`web/routers/generation/replicate.py` is the only caller — see its own
module docstring and `web/CLAUDE.md` for how the two-stage job flow (intake/
analyze -> review/edit -> generate) is wired to this package. Both stages
are ordinary jobs, so they appear in the Action log like every other tool.
