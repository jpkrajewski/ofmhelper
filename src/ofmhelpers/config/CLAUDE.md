# config

Every env var is read here and nowhere else.

- `settings.py` — one `BaseSettings` group per module (Session, Web, Infra, KieAI, Downloaders, Instagram, Accounts, Aigen, Higgsfield, Vault, GDrive, Logging).
- `VaultSettings.keys` (`OFM_VAULT_KEY`): comma-separated Fernet keys, first encrypts (web/db/encrypted.py). Lose them = lose every /phones login.
- `InfraSettings`: `OFM_ENV` (production|development; only the server says production), `OFM_FAKE_EXTERNALS` (every outside service faked: e2e), `OFM_FAKES_DIR` (fakes' cross-process state).
- `GDriveSettings` (`OFM_GDRIVE_*`): service account key JSON (`SecretStr`, one line), root folder id, `OFM_GDRIVE_PRODUCTION_ROOT_FOLDER` (the live one, refused outside production), day timezone. `configured` = key + root set.
- `__init__.py` — `from ofmhelpers.config import settings`; each group is built fresh on access, so a test's `monkeypatch.setenv` applies immediately.

Use: `settings.<group>.<field>`.
