# clone_ai_check

Is a generated reel too glitchy to post? `check_clone(video) -> CloneAiCheck`. Pure service: no DB, no web (callers save it: `web/stores/clone_ai_checks.py`).

- `check.py` — the whole video as one contact sheet (`llm.frames.contact_sheet`: a still every `OFM_CLONE_AI_CHECK_EVERY_S` (1 s), first and last frame included, 2..`OFM_CLONE_AI_CHECK_MAX_FRAMES` (16), each `OFM_CLONE_AI_CHECK_FRAME_PX` (360) wide and labelled with its time), `RULES` as the system prompt, sent to `get_llm(LLMUse.VIDEO)` (`LLM_DEFAULT_VIDEO`, Gemini). User decision 2026-10-25: a contact sheet of the whole video, not the raw video; GOOD or GLITCHY + a percent. Never raises for the video or the model: an unreadable video, a failed sheet or any `LLMError` is `CloneAiCheck.error` with no verdict (`"<message> | <provider detail>"`). The sheet lives in a temp dir for the call only; `stills_at` + `frame_px` redraw it (`sheet_at`).
- `models.py` — `CloneAiVerdict` (what the model must answer, its JSON schema goes with the prompt; field order = writing order: `reasoning`, `glitches` [`at_s`, `what`, `severity` minor|blocking], `glitch_percent` 0-100 = how sure it is unpostable, `verdict` last so it follows from the reasoning), `CloneAiCheck` (the whole check as kept: verdict, glitches, reasoning, provider, model, prompt system + text, raw answer, attempts, sheet/LLM seconds, error).
- Debugging (user rule 2026-10-25: DB + Logfire, never report files): every check is a `clone_ai_checks` row (alembic a2b3c4d5e6f7); /clone-links' reviews also log one span `AI check {job_id}` (verdict, glitch_percent, folder, provider, model, check_error).

Callers: /clone-links (`web/clone_links/review.py`).
