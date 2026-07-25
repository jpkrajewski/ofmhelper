# Module purpose

Reel cloning pipeline: given a viral reel (link or file), download it,
extract frames + a word-level transcript, draft a Seedance 2.0 prompt
package ("script") to rebuild its format with the user's own character, and
fire the actual video generation through the existing kie.ai integration.

This replaces the old `src/ofmhelpers/reel-machine/` directory — a bundle of
Bash scripts + Markdown "Claude Skill" files (a downloaded product template)
that drove generation through the WaveSpeed CLI and only ran inside an
interactive Claude Code session. This package is plain, testable Python,
wired into the FastAPI web app at `/replicate` (see
`web/routers/replicate.py`), and generates through the same `KieAIClient`
every other tool in this repo uses (kie.ai, not WaveSpeed).

Pipeline order: `intake.py` (download/frames/transcribe) -> `teardown.py`
(group the transcript into timed beats) -> `llm/` (write the prompt package,
template by default, using the user-supplied `target`/`gender` -- see
`gender.py`) -> user edits the draft in the browser -> `generation.py` (fires
the real kie.ai Seedance 2 call). `pipeline.py` wires the first three steps
together for the web layer.

# Module files

- `intake.py` — `fetch_source` (local file or yt-dlp download, reuses
  `downloaders.generic.download`; a failed Instagram download gets a hint
  appended pointing at `/cookies` -- Instagram blocks most logged-out reel
  downloads, and this repo already has cookie-upload support for exactly
  that, see `web/routers/cookies.py`), `extract_frames` (ffmpeg subprocess
  via `_run_ffmpeg`, which surfaces real stderr instead of a bare
  `CalledProcessError`; 1fps frame sequence + a 4x4 contact sheet -- the
  contact-sheet command passes `-frames:v 1 -update 1` since it writes ONE
  static image, not a `%03d` sequence; some ffmpeg builds hard-error on the
  image2 muxer without this), `transcribe` (word-level transcript via
  faster-whisper), `probe_duration` (ffprobe subprocess -- the source reel's
  own duration, used so the generated clone always matches it exactly; no
  manual "Length" field anywhere in the web layer), `diarize` (optional
  pyannote.audio speaker labeling — skips gracefully if `HF_TOKEN`/pyannote
  aren't available), `run_intake` (orchestrates fetch -> frames ->
  transcript -> duration probe). `Transcript`/`Word`/`IntakeResult`
  dataclasses (`IntakeResult.duration` is the probed source length).
- `teardown.py` — `Teardown`/`Beat` dataclasses + `build_teardown_draft`:
  groups a transcript's words into beats by pause length (a gap >=
  `BEAT_GAP_S` starts a new beat) — a deterministic, no-LLM first pass at the
  "what happens each second" breakdown, built from the transcript alone (no
  vision). `viral_mechanic`/`camera_look` start as `(edit me)` placeholders
  here — see `pipeline.draft_script` below for how a vision-capable provider
  fills them in instead. `Teardown.main_subject` (default `""`) is a vision
  step's main-subject identification (gender/age/clothing/hair/accessories/
  position/framing) -- an internal targeting aid ONLY, used so the vision
  LLM tracks the right person's actions when a reel has more than one
  person/object in frame. It is never merged into
  `prompt_builder.build_prompt_package`'s PROMPT block -- identity in the
  actual generation still comes exclusively from the user's reference
  images, so this field only ever reaches the human via the review page's
  read-only "Main subject (detected)" row. `Teardown.subject_action`
  (default `""`) is DIFFERENT from `main_subject` -- it's the subject's
  actual physical motion across the whole clip (e.g. "walks away from the
  camera the entire clip, back turned"), and it DOES go into the generated
  PROMPT/SCENE-LOCKS blocks (motion/action isn't physical appearance), where
  it overrides `prompt_builder`'s default hardcoded "one stable pose the
  entire clip" line -- without this override every clone was locked static
  even when the source reel's subject visibly walks/moves the whole time.
- `looks.py` — `LOOKS: dict[str, Look]`, the 6 camera/lens presets (GoPro
  POV, phone selfie, DV camcorder, webcam, CCTV, third-person) ported from
  the old bundle's `looks.md`.
- `shapes.py` — `SHAPES: dict[str, Shape]`, the 6 proven
  voice-count/camera-holder/pacing patterns (solo monologue, duet-selfie,
  duet-POV, woman x woman, CTA talking-head, action/no-dialogue) ported from
  the old bundle's `shapes.md`. Text fields use `{label}`/`{noun}`/
  `{noun_cap}`/`{subject}`/`{object}`/`{possessive}`/`{tag}` placeholders
  instead of hardcoded "female"/"she"/"her" -- `render_shape(shape, gender)`
  fills them in for whichever `Gender` was picked (see `gender.py`). Called
  by `prompt_builder.build_prompt_package`, not by the web layer directly.
- `gender.py` — `Gender` dataclass + `GENDERS` registry (`"female"`/`"male"`/
  `"nonbinary"`, each carrying `label`/`noun`/`subject`/`object`/
  `possessive`/`tag`), `get_gender(name)` (falls back to `DEFAULT_GENDER =
  "female"` for an unknown/missing name), `render_text(template, gender)`
  (the `str.format` call `shapes.render_shape` uses per-field). This is
  pronoun/voice-register correctness ONLY -- never physical appearance; the
  RULE block below still says identity comes from the reference images alone.
- `prompt_builder.py` — `SETTINGS` (the settings-lock: 9:16, 480p draft ->
  720p final, empty reference audio/video) + `build_prompt_package(teardown,
  shape, look, duration, character_name="your character", target="",
  gender=DEFAULT_GENDER)`: renders the TRIMMED, ready-to-fire package block
  structure (SETUP/IMAGE REFERENCE MAP/PROMPT/VOICE & PACING/PER-SECOND
  TIMELINE/EFFECTS/SCENE-LOCKS/NEGATIVE/VIRAL MECHANIC/CAMERA LOOK) as plain
  text. TARGET/PERSONA, RULE, SHAPE, PREFLIGHT, and COST/RISK are
  deliberately NOT rendered into the output -- they were internal authoring
  scaffolding that a VA firing the final prompt never needed; the RULE
  constraint they encoded ("never describe the person, identity = reference
  images only") is still enforced upstream in
  `llm/groq_provider.py`'s ANALYZE_SYSTEM_PROMPT/WRITE_SYSTEM_PROMPT, so
  removing the block from the output doesn't loosen anything. This is the
  free "template" LLM provider's implementation. `target` (a persona/tone
  brief) and `shape`/`look`'s `.name`/`.notes` no longer appear as their own
  blocks either, but `shape`/`gender` still drive the PROMPT/VOICE & PACING/
  timeline content. `gender` resolves via `get_gender` + `shapes.render_shape`
  before the shape is interpolated, and also picks the per-second timeline's
  speaker tag (see `_format_timeline`: a beat's default `speaker="MAIN"` --
  see `teardown.Beat` -- resolves to the gender's `tag`; an explicit
  non-"MAIN" speaker, e.g. a second voice typed in by hand, is left
  untouched). The PROMPT block's pose/motion line defaults to a hardcoded
  "one stable pose the entire clip -- no walk-in, no walking, no pose
  change", UNLESS `teardown.subject_action` is set (see `teardown.py`
  above), in which case that real detected motion replaces it -- same
  override in the SCENE-LOCKS `[POSE]` line.
- `llm/` — pluggable script-writer providers, see below.
- `generation.py` — `generate_reel_clone(...)`: uploads character reference
  images and calls the existing `KieAIClient.generate_video_seedance2` (no
  separate HTTP client — reuses `aigenproviders/kaiai/client.py`). Defaults
  to `aspect_ratio="9:16"` (reels are vertical, unlike `web/routers/seedance.py`'s
  `16:9` default).
- `pipeline.py` — `intake_reel` / `draft_script_full(intake, shape_key,
  look_key, duration=None, llm_provider, target="", gender=DEFAULT_GENDER)
  -> DraftResult(script, main_subject)`: the entry points
  `web/routers/replicate.py` actually calls (`draft_script(...) -> str` is a
  thin back-compat wrapper returning just `.script`, kept for callers that
  don't need `main_subject`). `duration` defaults to `None`, meaning "derive
  from `intake.duration` via `clamp_duration` (Seedance's 4-15s supported
  range)" -- the generated clone always matches the source reel's own
  length; there is no manual duration parameter exposed anywhere in the web
  layer. `draft_script_full` runs two provider calls, each with its own
  fallback: `analyze_reel` (VISION — looks at the full video when the
  provider supports it, else the contact sheet + transcript, to fill in
  `main_subject`/hook/viral_mechanic/camera_look; on failure or with the
  template provider, just leaves the `(edit me)` placeholders in place)
  then `write_prompt_package(..., target=target, gender=gender)` (falls
  back to `TemplateProvider` on any failure). A broken/misconfigured LLM
  choice degrades script quality — it never fails the job.

## `llm/` — pluggable script-writer providers

Two capabilities per provider, since they have very different costs:
`analyze_reel(contact_sheet, transcript_text, video_path=None) -> dict`
(VISION — the "watch the reel" step the old bundle's interactive agent did
by eye; returns `{"main_subject", "hook", "subject_action", "viral_mechanic",
"camera_look"}` — `main_subject` is a gender/age/clothing/hair/accessories/
position/framing description used purely to anchor the analysis onto the
right person when a reel has more than one in frame, never written into the
generated prompt text; `subject_action` is the main subject's actual
physical motion for the whole clip (e.g. "walks away from the camera the
entire clip, back turned") and, unlike `main_subject`, DOES get written into
the generated PROMPT/SCENE-LOCKS blocks, overriding
`prompt_builder.build_prompt_package`'s default static-pose line -- see
`teardown.py` above) and `write_prompt_package(teardown,
shape, look, duration, target="", gender=DEFAULT_GENDER) -> str`
(writes/punches up the actual prompt text -- `target`/`gender` are
forwarded straight to `prompt_builder.build_prompt_package`, and for
Groq/Gemini/Anthropic also passed to the model so a rewrite doesn't drift
the persona/pronouns the draft already locked in; every LLM-backed
provider's return value is passed through `base.strip_llm_preamble` before
returning, so commentary a model prepends/wraps around the package -- e.g.
"Here is the rewritten prompt:", a ``` code fence -- never leaks into the
user-facing textarea).

- `base.py` — the `LLMProvider` Protocol for both methods above, plus
  `strip_llm_preamble(text, draft)` (the shared leak-guard described above
  -- cuts everything before the first "SETUP" line, since the package
  structure is fixed; returns `draft` unchanged if "SETUP" is missing
  entirely, i.e. the response wasn't a valid rewrite at all).
- `template_provider.py` — `TemplateProvider` (`name = "template"`): the
  default, always-available, zero-cost provider. **No vision** —
  `analyze_reel` is a no-op (returns `{}`), so the `(edit me)` placeholders
  from `teardown.build_teardown_draft` survive untouched.
  `write_prompt_package` wraps `prompt_builder.build_prompt_package`
  directly.
- `groq_provider.py` — `GroqProvider` (`name = "groq"`): free-tier via Groq's
  OpenAI-compatible API (`GROQ_API_KEY`). `write_prompt_package` uses a fast
  text model (`text_model`, default `llama-3.3-70b-versatile`) to punch up
  dialogue — this works fine. `analyze_reel` is a **no-op by default**:
  confirmed against the account's live `GET /models` response that Groq's
  current catalog has no vision-capable model at all (no llama-vision, no
  llama-4-scout — their vision preview models were retired and not
  replaced), so guessing a model id here just produces a 404 every time.
  Pass `vision_model=` (or set `GROQ_VISION_MODEL`) if Groq ever adds one
  back / your account gets access to one. Also owns the shared
  `ANALYZE_SYSTEM_PROMPT`/`WRITE_SYSTEM_PROMPT` strings that
  gemini_provider.py/anthropic_provider.py import — `ANALYZE_SYSTEM_PROMPT`
  is the prompt driving the main-subject-then-actions analysis described
  above.
- `gemini_provider.py` — `GeminiProvider` (`name = "gemini"`): the free
  provider that actually DOES vision today. Google's Gemini API
  (`GEMINI_API_KEY` from https://aistudio.google.com/apikey) has a genuine
  free tier including image input, used for both `analyze_reel` and
  `write_prompt_package`. `analyze_reel` prefers sending the **actual video**
  (via `client.files.upload` + polling for `state == "ACTIVE"`, see
  `_upload_video_part`) instead of just the static contact sheet -- a
  static image loses motion/timing/transitions, which is what made action
  descriptions unreliable; falls back to the contact-sheet image (the old
  behavior) if no `video_path` is given, or the upload/processing step
  fails/times out (`_VIDEO_ACTIVE_TIMEOUT_S`), so a flaky upload never
  fails the intake job. Defaults to the `gemini-flash-latest` ALIAS (not a
  pinned dated model id) -- Google documents this as always pointing at
  their current flash release, specifically so this doesn't break again the
  way pinning `gemini-2.0-flash` did when Google retired it (free-tier calls
  started 429ing with a `limit: 0` quota instead of a normal "used it all
  up" error). Override with `GEMINI_MODEL` if needed. Unrelated to
  `gdrive/` — that's OAuth-as-a-user Drive access, this is a plain API-key
  call.
- `anthropic_provider.py` — `AnthropicProvider` (`name = "anthropic"`): same
  two-capability shape via the Claude API's native vision support
  (`ANTHROPIC_API_KEY`). Claude's Messages API vision is image-only (no
  native video input), so `analyze_reel` always uses the contact sheet --
  accepts `video_path` only to satisfy the shared `LLMProvider` interface,
  never uses it. Billed separately per-token from a claude.ai subscription —
  opt-in only, not installed by default (needs
  `pip install 'ofmhelpers[llm-anthropic]'`).
- `registry.py` — `get_provider(name=None)`: resolves a provider by explicit
  name or the `REEL_MACHINE_LLM_PROVIDER` env var, defaulting to
  `"template"`. Falls back to `TemplateProvider` immediately if construction
  fails (missing package/API key) — see also `pipeline.draft_script_full`'s
  runtime fallback for failures during the actual `analyze_reel`/
  `write_prompt_package` calls.

# Dependencies

`faster-whisper`, `groq`, and `google-genai` (Gemini) are base dependencies
(all free tier). `pyannote.audio` + `torch` (diarization) and `anthropic`
(paid LLM option) are optional-extras groups in `pyproject.toml`
(`[project.optional-dependencies]`) — never installed unless explicitly
requested, since they're either heavy or paid.

# Who calls this

`web/routers/replicate.py` is the only caller — see its own module docstring
and `web/CLAUDE.md` for how the two-stage job flow (intake -> review/edit ->
generate) is wired to this package.
