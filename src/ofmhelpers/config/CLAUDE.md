# config

Every env var is read here and nowhere else.

- `settings.py` — one `BaseSettings` group per module (Session, Web, Infra, KieAI, Downloaders, Instagram, Accounts, Aigen, Higgsfield, Vault, GDrive, Logging).
- `VaultSettings.keys` (`OFM_VAULT_KEY`): comma-separated Fernet keys, first encrypts (web/db/encrypted.py). Lose them = lose every /phones login.
- `InfraSettings`: `OFM_ENV` (production|development; only the server says production), `OFM_FAKE_EXTERNALS` (every outside service faked: e2e), `OFM_FAKES_DIR` (fakes' cross-process state).
- `GDriveSettings` (`OFM_GDRIVE_*`): no folder id for the app: each owner's own Drive (Google sign-in), root found by name (`web/drive_folders.root_name()`, "DEV - " prefix outside production); POSTED folder name, day timezone, sweep interval, retries/timeouts. Service account key JSON (`SecretStr`, one line) + root folder id are only for the live Drive contract tests.
- `LinksSettings` (`OOPSIE_API_KEY_ADMIN`, `BOUNCY_API_KEY_ADMIN`, `OFM_LINKS_*`): admin fallback keys for /links, sync interval 3 h, claim/create TTLs, periods 1/7/30/90 d (default 7), page sizes, max links per Generate (100). See links/CLAUDE.md.
- `LLMSettings` (`LLM_DEFAULT_VIDEO` gemini, `LLM_DEFAULT_CHAT` groq, each `<provider>[:<model>]`; `GEMINI_API_KEY`/`GROQ_API_KEY`/`CEREBRAS_API_KEY` + `OFM_<PROVIDER>_MODEL`; `OFM_LLM_*` retries, timeouts, inline/image/contact-sheet limits): see llm/CLAUDE.md.
- `CloneAiCheckSettings` (`OFM_CLONE_AI_CHECK_*`: a still every 1 s, at most 16, 360 px, ffmpeg timeout): see clone_ai_check/CLAUDE.md.
- `CloneLinksSettings` (`OFM_CLONE_LINKS_*`): links per paste (120: a VA's real list was 112; each is previewed, 6 at once), the review folders in the clicker's root (`GOOD REELS`, `TRIAL REELS`), share-tracker query params Check drops (`utm_*` = prefix), a review running longer than `REVIEW_STALE_S` (1 h) = cut off.
- `SignInSettings` (`OFM_ADMIN_EMAILS`, `OFM_GOOGLE_CLIENT_*`, `OFM_APPLE_*`, password length/link TTL): see signin/CLAUDE.md.
- `__init__.py` — `from ofmhelpers.config import settings`; a group is rebuilt only when the process environment, its `env_file` or that file's mtime (checked at most every second) changed, so a test's `monkeypatch.setenv` applies immediately. Rebuilding on every access cost /reels 310 builds (~0.4 s) per request (prod profile 2026-10-22).

Use: `settings.<group>.<field>`.
