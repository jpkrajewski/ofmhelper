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
  faster-whisper), `diarize` (optional pyannote.audio speaker labeling —
  skips gracefully if `HF_TOKEN`/pyannote aren't available), `run_intake`
  (orchestrates fetch -> frames -> transcript). `Transcript`/`Word`/
  `IntakeResult` dataclasses.
- `teardown.py` — `Teardown`/`Beat` dataclasses + `build_teardown_draft`:
  groups a transcript's words into beats by pause length (a gap >=
  `BEAT_GAP_S` starts a new beat) — a deterministic, no-LLM first pass at the
  "what happens each second" breakdown, built from the transcript alone (no
  vision). `viral_mechanic`/`camera_look` start as `(edit me)` placeholders
  here — see `pipeline.draft_script` below for how a vision-capable provider
  fills them in instead.
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
  gender=DEFAULT_GENDER)`: renders the full package-spec block structure
  (SETUP/**TARGET & PERSONA**/RULE/IMAGE REFERENCE MAP/SHAPE/PROMPT/VOICE &
  PACING/PER-SECOND TIMELINE/EFFECTS/SCENE-LOCKS/NEGATIVE/PREFLIGHT/VIRAL
  MECHANIC/CAMERA LOOK/COST) as plain text. This is the free "template" LLM
  provider's implementation. `target` is a persona/tone brief (e.g.
  "confident fitness coach pitching a program") written into its own TARGET
  / PERSONA block -- it steers dialogue delivery only, same "never physical
  appearance" rule as everything else. `gender` resolves via `get_gender` +
  `shapes.render_shape` before the shape is interpolated, and also picks the
  per-second timeline's speaker tag (see `_format_timeline`: a beat's default
  `speaker="MAIN"` -- see `teardown.Beat` -- resolves to the gender's `tag`;
  an explicit non-"MAIN" speaker, e.g. a second voice typed in by hand, is
  left untouched).
- `llm/` — pluggable script-writer providers, see below.
- `generation.py` — `generate_reel_clone(...)`: uploads character reference
  images and calls the existing `KieAIClient.generate_video_seedance2` (no
  separate HTTP client — reuses `aigenproviders/kaiai/client.py`). Defaults
  to `aspect_ratio="9:16"` (reels are vertical, unlike `web/routers/seedance.py`'s
  `16:9` default).
- `pipeline.py` — `intake_reel` / `draft_script(intake, shape_key, look_key,
  duration, llm_provider, target="", gender=DEFAULT_GENDER)`: the two entry
  points `web/routers/replicate.py` actually calls. `draft_script` runs two
  provider calls, each with its own fallback: `analyze_reel` (VISION —
  looks at the contact sheet + transcript to fill in the hook/
  viral_mechanic/camera_look; on failure or with the template provider,
  just leaves the `(edit me)` placeholders in place) then
  `write_prompt_package(..., target=target, gender=gender)` (falls back to
  `TemplateProvider` on any failure). A broken/misconfigured LLM choice
  degrades script quality — it never fails the job.

## `llm/` — pluggable script-writer providers

Two capabilities per provider, since they have very different costs:
`analyze_reel(contact_sheet, transcript_text) -> dict` (VISION — the "watch
the reel" step the old bundle's interactive agent did by eye; returns
`{"hook", "viral_mechanic", "camera_look"}`) and
`write_prompt_package(teardown, shape, look, duration, target="",
gender=DEFAULT_GENDER) -> str` (writes/punches up the actual prompt text --
`target`/`gender` are forwarded straight to `prompt_builder.build_prompt_package`,
and for Groq/Gemini/Anthropic also passed to the model so a rewrite doesn't
drift the persona/pronouns the draft already locked in).

- `base.py` — the `LLMProvider` Protocol for both methods above.
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
  back / your account gets access to one.
- `gemini_provider.py` — `GeminiProvider` (`name = "gemini"`): the free
  provider that actually DOES vision today. Google's Gemini API
  (`GEMINI_API_KEY` from https://aistudio.google.com/apikey) has a genuine
  free tier including image input, used for both `analyze_reel` and
  `write_prompt_package`. Defaults to the `gemini-flash-latest` ALIAS (not a
  pinned dated model id) -- Google documents this as always pointing at
  their current flash release, specifically so this doesn't break again the
  way pinning `gemini-2.0-flash` did when Google retired it (free-tier calls
  started 429ing with a `limit: 0` quota instead of a normal "used it all
  up" error). Override with `GEMINI_MODEL` if needed. Unrelated to
  `gdrive/` — that's OAuth-as-a-user Drive access, this is a plain API-key
  call.
- `anthropic_provider.py` — `AnthropicProvider` (`name = "anthropic"`): same
  two-capability shape via the Claude API's native vision support
  (`ANTHROPIC_API_KEY`). Billed separately per-token from a claude.ai
  subscription — opt-in only, not installed by default (needs
  `pip install 'ofmhelpers[llm-anthropic]'`).
- `registry.py` — `get_provider(name=None)`: resolves a provider by explicit
  name or the `REEL_MACHINE_LLM_PROVIDER` env var, defaulting to
  `"template"`. Falls back to `TemplateProvider` immediately if construction
  fails (missing package/API key) — see also `pipeline.draft_script`'s
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
