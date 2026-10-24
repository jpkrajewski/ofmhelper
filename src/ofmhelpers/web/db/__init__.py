"""
Persistence layer for the web app's durable state, backed by Postgres.

- models.py       -- SQLAlchemy ORM tables (the schema Alembic manages)
- session.py      -- lazy engine + sessionmaker built from settings.infra
- encrypted.py    -- `EncryptedText`, the Fernet column the /phones vault uses
- repositories/   -- the ONLY code allowed to touch the DB, one module per
                     table group, cached reads through `cached_repository.py`
"""
