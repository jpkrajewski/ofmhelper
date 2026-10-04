"""
Persistence layer for the web app's durable stores (jobs, landing-page
applications), backed by Postgres.

- models.py     -- SQLAlchemy ORM tables (the schema Alembic manages)
- session.py    -- lazy engine + sessionmaker built from settings.infra
- repository.py -- the ONLY code allowed to touch the DB (added in step 4)
"""
