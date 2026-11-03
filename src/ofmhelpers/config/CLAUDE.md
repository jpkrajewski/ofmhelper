# config

Every env var is read here and nowhere else.

- `settings.py` — one `BaseSettings` group per module (Session, Web, Infra, KieAI, Downloaders, Instagram, Accounts, Aigen, Higgsfield, Vault, GDrive, Logging).
- `VaultSettings.keys` (`OFM_VAULT_KEY`): comma-separated Fernet keys, first encrypts (db/encrypted.py). Lose them = lose every /phones login.
- `InfraSettings`: `OFM_ENV` (production|development; only the server says production), `OFM_FAKE_EXTERNALS` (every outside service faked: e2e), `OFM_FAKES_DIR` (fakes' cross-process state), `OFM_RQ_WORKERS` / `OFM_CLONE_LINKS_WORKERS` (5: the clone-links queue's own pool).
- `GDriveSettings` (`OFM_GDRIVE_*`): no folder id for the app: each owner's own Drive (Google sign-in), root found by name (`features/drive/folders.root_name()`, "DEV - " prefix outside production); POSTED folder name, day timezone, sweep interval, retries/timeouts. Service account key JSON (`SecretStr`, one line) + root folder id are only for the live Drive contract tests.
- `LinksSettings` (`OOPSIE_API_KEY_ADMIN`, `BOUNCY_API_KEY_ADMIN`, `OFM_LINKS_*`): admin fallback keys for /links, sync interval 3 h, claim/create TTLs, periods 1/7/30/90 d (default 7), page sizes, max links per Generate (100). See links/CLAUDE.md.
- `LLMSettings` (`LLM_DEFAULT_VIDEO` gemini, `LLM_DEFAULT_CHAT` groq, each `<provider>[:<model>]`; `GEMINI_API_KEY`/`GROQ_API_KEY`/`CEREBRAS_API_KEY` + `OFM_<PROVIDER>_MODEL`; `OFM_LLM_*` retries, timeouts, inline/image/contact-sheet limits): see llm/CLAUDE.md.
- `CloneAiCheckSettings` (`OFM_CLONE_AI_CHECK_*`: a still every 1 s, at most 16, 360 px, ffmpeg timeout, whole-check deadline 600 s, GLITCHY at >= 50%): see clone_ai_check/CLAUDE.md.
- `CloneLinksSettings` (`OFM_CLONE_LINKS_*`): links per paste (120), preview reads at once (6) and its deadline (90 s) / socket timeout, single-post extractors Check takes, review folders (`GOOD REELS`, `TRIAL REELS`), share-tracker params Check drops (`utm_*` = prefix), `REEL_MB` (Drive-room warning), halt TTL, review RQ limit (1200 s), a claimed review older than `REVIEW_STALE_S` (1800 s) = cut off and re-queued, up to `REVIEW_ATTEMPTS` (3).
- `PicturesSettings` (`OFM_PICTURES_*`: preset images max 10, pictures per submit 20, resolution 2K, aspect ratio 3:4): see web/CLAUDE.md /pictures.
- `RunningHubSettings` (`RUNNINGHUB_API_KEY_ADMIN`, `OFM_RH_*`: base URL, request timeout, upload cache TTL 20 h (uploads are signed 24 h), reference image limits): see integrations/ai_providers/CLAUDE.md.
- `ElyumSettings` (`ELYUM_API_KEY_ADMIN`, `OFM_ELYUM_*`: base URL `https://api.elyum.ai`, request timeout, upload cache TTL 20 h, max file 60 MB, image side 8000, .mkv/.m4v convert timeout): see integrations/ai_providers/CLAUDE.md.
- `WebSettings` /clone prices (dollars per source second = credits x $/credit): `OFM_CLONE_CREDITS_PER_VIDEO_S` + `OFM_KIE_USD_PER_CREDIT`, `OFM_CLONE_HF_CREDITS_PER_VIDEO_S` + `OFM_HF_USD_PER_CREDIT`, `OFM_CLONE_RH_USD_PER_VIDEO_S`, `OFM_CLONE_ELYUM_USD_PER_VIDEO_S` (JSON per resolution).
- `ViralSettings` (`APIFY_API_KEYS` comma-separated -- the name the server's .env has held since the lead hunt; `OFM_VIRAL_*`: actor `apify~instagram-reel-scraper`, `SCAN` 200 newest reels read, `TOP` 30 kept, run timeout 900 s, download workers/timeout, Free-plan `USD_PER_REEL` 0.0026 / `USD_PER_RUN` 0.001 for the page's "up to $X"): see viral/CLAUDE.md.
- `SignInSettings` (`OFM_ADMIN_EMAILS`, `OFM_GOOGLE_CLIENT_*`, `OFM_APPLE_*`, password length/link TTL): see signin/CLAUDE.md.
- `__init__.py` — `from ofmhelpers.config import settings`; a group is rebuilt only when the process environment, its `env_file` or that file's mtime (checked at most every second) changed, so a test's `monkeypatch.setenv` applies immediately. Rebuilding on every access cost /reels 310 builds (~0.4 s) per request (prod profile 2026-10-22).

Use: `settings.<group>.<field>`.
