# Module purpose

The app's configuration: every env var is read here and nowhere else.

# Module files

- `settings.py` — one `BaseSettings` group per app module (`SessionSettings`,
  `WebSettings`, `InfraSettings`, `KieAISettings`, `DownloadersSettings`,
  `LoggingSettings`, `LeadHuntSettings`).
- `__init__.py` — the single import point, `from ofmhelpers.config import
  settings`. Each group property constructs its class fresh on access (a lazy
  global, not a cached one) so a test's `monkeypatch.setenv` is seen by the
  very next call.

# Who calls this

Everything that needs a setting, via `settings.<group>.<field>`.
