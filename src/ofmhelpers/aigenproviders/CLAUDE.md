# aigenproviders

AI providers behind one interface; every model is catalog data. The web app never names a provider or model field.
Providers: kie.ai (`kaiai/`), Higgsfield (`higgsfield/`), fake (`fake/`, free, for tests/pages).

## Shared layer
- `base.py` — `Provider`: `upload`, `submit(spec, payload, idempotency_key)`, `status -> TaskState`, `download`, `default_key(admin)`; class vars `name`/`label`/`rank`/`needs_key`. `@register_provider` into an `lru_cache`d registry; lookups `provider_class`, `get_provider`, `provider_names`.
- `spec.py` — `ModelSpec` (one catalog entry, `key = "<provider>:<model id>"`). Derives `media_fields`, `form_fields`, `parse_form`, `check` (jsonschema 2020-12, before any upload/payment), `request`.
- `catalog.py` — `catalog()`, `get_spec(key)`, `models_for(provider, kind)`.
- `runner.py` — `generate(...)`: resume or submit, poll with backoff, download to `settings.aigen.out_dir`. Timeout leaves the request remembered for recovery.
- `tasks.py` — `ProviderTask` per job in Redis (`aigen:task:<job id>`), incl. the key it was made with. Redis errors logged, never raised.
- `errors.py` — `InvalidInputError` + `ProviderError` family (auth/input/generation/unavailable/timeout). Adapters map codes onto these. A rejected key is always `ProviderAuthError(label, detail)` = "<label>: wrong API key" (provider text in `.detail` + exception note, for logs).

## Adding a provider
1. `<name>/provider.py` (`@register_provider`), `models.py` (wire format, `extra="ignore"`), `client.py` only without an official SDK.
2. `<name>/catalog.json`: `{model_id, label, family, kind, input_schema[, fixed, default_prompt]}`; generate it if the provider publishes schemas.
3. Map failures to `errors.py`, statuses to `TaskState`. Wire-level faked tests.
4. `<name>/fake.py`: the provider's wire faked for OFM_FAKE_EXTERNALS (requests via `utils/outbound.py` routes, httpx via a `MockTransport`), one `FAKE_KEY` accepted; add it to `tests/contract/test_ai_provider_contract.py`.
Nothing else changes. For /clone: one `CloneTarget` in `clone.py`.

## Fakes (OFM_FAKE_EXTERNALS)
- `kaiai/fake.py` — upload/createTask/recordInfo/result host; `FAKE_KEY` only (else code 401); "generating" for 2 polls, then success.
- `higgsfield/fake.py` — `install(client)` puts an httpx `MockTransport` under the SDK (its `_client`/`_upload_client` cached properties, higgsfield-client 0.2.0); `FAKE_KEY` only (else 401); queued 2 polls, then completed; result/upload host served to `requests` too.
- `base.Provider.download` goes through `utils/outbound.get`.
- Wire behaviour (success after 3 polls, 4xx, 5xx, timeout, rate limit, failed, rerun resumes, never resubmits): `tests/integration/aigenproviders/test_provider_wire_behaviour.py`, bodies in `tests/fixtures/providers/`.

## kie.ai (`kaiai/`, typo kept for imports)
- `client.py` — retries GET/HEAD only (a createTask is never resent — paid twice). Errors via `models.raise_for` (401/403 auth, 408/429/455/5xx unavailable, else input; 402 = no credits). Audit logs `tasks.jsonl`, `completions.jsonl`. Upload memo in Redis keyed by key+path, HEAD-checked; oversized images uploaded as a shrunk JPEG copy.
- Keys `KIE_AI_API_KEY_ADMIN`/`_VA`. No idempotency key; re-runs resume.
- `catalog.json` — 17 models; old per-model hard-codes are data (`fixed`, split entries).

## Higgsfield (`higgsfield/`)
- Official SDK `higgsfield-client` 0.2.0. Key `KEY_ID:KEY_SECRET` (`HF_API_KEY_ADMIN`/`_VA`). Uploads only JPEG/PNG/WebP/GIF/WAV/MP4.
- `catalog.json` generated (83 models) by `scripts/sync_higgsfield_catalog.py`.
- Findings (2026-10-18, docs.higgsfield.ai): base `https://api.higgsfield.ai`, header `Authorization: Key <id>:<secret>`; statuses queued/in_progress/completed/failed/nsfw/canceled; uploads via presigned URL (1 h, PUT without key); outputs kept ≥7 days. The SDK retries submits without an Idempotency-Key — rare double charge possible; a job never resubmits once it has a request id.

Callers: `web/routers/generation/ai.py`, `clone.py`, `web/recovery.py`, `web/api_keys.py`, `web/schemas/generation.py`.
