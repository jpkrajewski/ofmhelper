# config

Every env var is read here and nowhere else.

- `settings.py` — one `BaseSettings` group per module (Session, Web, Infra, KieAI, Downloaders, Instagram, Aigen, Higgsfield, Logging).
- `__init__.py` — `from ofmhelpers.config import settings`; each group is built fresh on access, so a test's `monkeypatch.setenv` applies immediately.

Use: `settings.<group>.<field>`.
