# llm

Language models that read text, images and video and answer in a typed shape, behind one interface. The app never names a provider or model.

```python
from ofmhelpers.llm import LLMUse, Prompt, get_llm

found = get_llm(LLMUse.VIDEO).ask(
    Prompt.of("Subject:", photo, "Reel:", video, system=RULES), Verdict
)
```

## Shared layer
- `models.py` — `Prompt` (`parts` in order: `TextPart` / `MediaPart`, `system`, `temperature` 0), `Prompt.of(*str|Path)`, `MediaPart.of(path)` (kind from the file name; not image/video/audio = `LLMInputError`), `MediaKind`, `LLMUse` (VIDEO, CHAT).
- `base.py` — `LLMProvider`: class vars `name`, `label`, `media` (kinds it reads), `max_images`; `default_key()`/`default_model()` (settings); `_ask` = one attempt returning JSON text. `ask(prompt, Answer)` does the rest once for every provider: a video for an images-only provider becomes a note + contact sheet (`frames.py`), media it cannot read / too many images refused before sending, `LLMUnavailableError` retried (`OFM_LLM_RETRIES` 4; the provider's retry hint, else 5 s doubling, max 60 s; a hint over the max -- a daily quota gone -- fails at once), the answer validated (`LLMOutputError`). Registry like aigenproviders: `@register_llm`, `llm_names`, `llm_class`. `get_llm(use, provider=, api_key=, model=)`: the default for `use` (`LLM_DEFAULT_VIDEO` = gemini, `LLM_DEFAULT_CHAT` = groq), each `<provider>[:<model>]`; no key = `LLMConfigError`. No fallback chain (user decision 2026-10-25): one default per kind of work.
- `errors.py` — `LLMError(message, detail)` family: `LLMConfigError` (no key, unknown provider/model), `LLMAuthError` ("<label>: wrong API key"), `LLMInputError`, `LLMRefusedError` (safety filter), `LLMOutputError`, `LLMUnavailableError` (retryable, `retry_after_s`). Provider wording in `.detail` + exception note, never in the message.
- `frames.py` — `contact_sheet(video, dest, frames, frame_px, timeout)`: `frames` stills from the first frame to the last (`still_times`; the last 0.1 s before the end, a seek to the very end decodes nothing), one ffmpeg seek each, tiled in time order with PIL, each labelled with its time ("0:07.5") so a model can say where. Motion between stills is lost. Used for images-only providers (`OFM_LLM_VIDEO_SHEET_FRAMES` 9) and by `clone_ai_check`.
- `fakes.py` — what every fake shares: `example(schema)` (the plainest value a JSON Schema allows: true, 0, "", [], first enum value -- a checker's fake answer reads "passed"), `field(camel, snake)`.
- `chat_completions.py` — `ChatCompletionsLLM`: OpenAI-style `POST <base_url>/chat/completions` over httpx (no vendor SDK: one wire for every vendor; each SDK, or OpenAI's v3 with its httpx2 fork and daily releases, would be a dependency for one endpoint). Images inline as base64 data URLs, shrunk to `OFM_LLM_IMAGE_MAX_SIDE` 1024 / 3 MB (free plans count tokens per minute). `response_format` strict JSON Schema via `strict_schema` (refs inlined, objects closed, all properties required, Cerebras' rejected keywords dropped). Errors: 401/403 auth, 404 config, 429 unavailable (`retry-after`), 408/409/5xx unavailable, else input; `finish_reason: content_filter` = refused. A vendor = subclass (`base_url`, `max_images`, key/model/fake hooks).
- `chat_completions_fake.py` — `FakeChatCompletions(host, key, models, max_images)`: 401 other key, 404 unknown model, 400 over the image cap or a non-strict schema; queued `answer_next`/`fail_next`/`filter_next`, else `example`.

## Providers
- `gemini/` — Google AI Studio, `google-genai` SDK (2.29.0). The only free tier that watches real video. `gemini-3.8-flash` (`OFM_GEMINI_MODEL`), `GEMINI_API_KEY`. Media <= `OFM_LLM_INLINE_MAX_BYTES` (14 MB) inline, else Files API upload -> poll until ACTIVE (`OFM_LLM_FILE_READY_TIMEOUT_S`) -> deleted after the answer. `response_json_schema`. SDK retries off (base retries). Errors: 400 "API key not valid"/401/403 auth, 404 config, 429 unavailable with `RetryInfo.retryDelay`, 408/499/5xx unavailable, else input; `promptFeedback.blockReason` / safety finish reasons = refused. Fake (`fake.py`): wire under the SDK's `HttpOptions.httpx_client` -- generateContent, resumable upload, files get/delete; FAKE_KEY only; `MODELS` else 404; a fileData part naming a file not ACTIVE = 400; hooks `answer_next`, `fail_next(code, status, msg, retry_delay_s)`, `refuse_next`, `fail_processing`, `polls_until_active`.
- `groq/` — `ChatCompletionsLLM` at `https://api.groq.com/openai/v1`, `qwen/qwen3.8-27b` (`OFM_GROQ_MODEL`), `GROQ_API_KEY`, images only, max 3.
- `cerebras/` — `ChatCompletionsLLM` at `https://api.cerebras.ai/v1`, `qwen-3.8-27b` (`OFM_CEREBRAS_MODEL`), `CEREBRAS_API_KEY`, images only, max 2.

## Adding a provider
OpenAI-style API: `<name>/provider.py` subclassing `ChatCompletionsLLM` + `<name>/fake.py` (a `FakeChatCompletions`). Other wire: subclass `LLMProvider`, write `_ask` mapping failures onto `errors.py`, a wire fake. Either way: key/model fields in `LLMSettings`, the fake in both conftests' cache lists, an entry in `tests/contract/test_llm_contract.py` PROVIDERS. Nothing else changes.

## Findings (2026-10-24/25)
- Gemini free tier (ai.google.dev/gemini-api/docs/pricing): `gemini-3.8-flash`, `3.6-flash`, `3.5-flash-lite`, `3.1-flash-lite` free; free-tier inputs "used to improve our products". 2.0 models shut down, 2.5 only for past users. Rate limits per project, shown in AI Studio only. Video: 1 fps sampled, ~300 tokens/s (100 at low resolution); inline under 20 MB a request.
- google-genai 2.29.0 writes `inlineData.mime_type` / `fileData.file_uri` snake case inside camelCase parts (Google's parser takes both); the request config is camelCase (`responseJsonSchema`, `systemInstruction`). Upload: `POST /upload/v1beta/files` -> `x-goog-upload-url` header -> chunks with `X-Goog-Upload-Command: upload[, finalize]`, answered `x-goog-upload-status: active|final`, body `{"file": ...}`.
- Groq (console.groq.com/docs): vision = `qwen/qwen3.8-27b` (max 3 images, 20 MB a URL request) and `meta-models/muse-spark-open` (limited preview, 5). Free: 30 RPM, 1K RPD, 8K TPM, 200K TPD, per organization. Strict JSON Schema on qwen3.8-27b, muse-spark-open, gpt-oss-20b/120b; no streaming with it. Qwen 3.8 returns no reasoning at default effort; JSON mode needs `reasoning_format` parsed/hidden if set (raw = 400). Data: not retained by default, logs up to 30 days, Zero Data Retention switch.
- Cerebras (inference-docs.cerebras.ai): `qwen-3.8-27b` (images: 2 a request, 10 MiB payload) and `gpt-oss-120b`; Free Trial 5 RPM, 30K uncached TPM, 1M TPD. Strict schema: no `oneOf/allOf/not/pattern/format/minItems/maxItems/patternProperties`, local non-recursive `$ref` only, depth 10.
- Measured on dev 2026-10-25 (two real 6-8 s reels, one 9:16 contact sheet of 7-9 stills each, free tier): `gemini-3.8-flash` answered in 60 s and 87 s, one attempt each, verdict + reasoning sensible; the sheet took ~4 s. Hence `OFM_LLM_TIMEOUT_S` 300 (was 120).
- GitHub Models: retired 2026-07-30. OpenRouter `:free`: 50 requests/day without bought credits. Mistral free plan: reportedly requires opting into training (community source, unverified).

Callers: `clone_ai_check.check_clone` (/clone-links' review of every result).
