# config

Every env var is read here and nowhere else.

- `settings.py` — one `BaseSettings` group per module (Session, Web, Infra, KieAI, Downloaders, Instagram, Accounts, Aigen, Higgsfield, Vault, Logging).
- `VaultSettings.keys` (`OFM_VAULT_KEY`): comma-separated Fernet keys, first encrypts (web/db/encrypted.py). Lose them = lose every /phones login.
- `__init__.py` — `from ofmhelpers.config import settings`; each group is built fresh on access, so a test's `monkeypatch.setenv` applies immediately.

Use: `settings.<group>.<field>`.
