---
paths:
  - "src/ofmhelpers/db/**"
  - "alembic/**"
---

# Database: MUST

Sources: PostgreSQL wiki "Don't Do This" (https://wiki.postgresql.org/wiki/Don%27t_Do_This), `CLAUDE.md` "Delivery gates" 4.

## Plan before the migration
- Write down first, in the handoff: the tables, columns, types, keys, indexes, who writes and who reads them, and how it sits next to what exists. Read `db/models.py` for a place the fact already lives.
- One concept, one table, one name everywhere. One fact, one place: no copy of the same value in a second column or table.
- No column bolted on per feature. A new need that does not fit is a sign the table is wrong; say so.

## Shape
- Names: lower snake_case, singular meaning per row, `_at` for moments, `_date` for days, `_id` for keys, the same foreign-key name in every table.
- Types: `timestamptz` (never `timestamp` without zone), `text` (never `varchar(n)` or `char(n)`), `bigint`/identity keys, `numeric` for money (never `float`, never `money`), `boolean` not null with a default.
- Every relation is a foreign key with a chosen `ON DELETE`. Every lookup the app does has an index. Uniqueness is a constraint, not a check in code.
- JSONB only for data read back whole (job `params`, `result`, `meta`). Anything filtered, joined or summed is a real column.
- Not null unless absence means something.

## Access
- Only `db/repositories/` and `db/stores/` touch the database; they return models. Services never see a session.
- Caching only through `CachedRepository` + `@cached` / `@invalidates_cache`.
- Queries filter by owner: every user, admin included, sees only their own rows.
- Filtering, joining and counting happen in SQL, not in Python over fetched rows. No `NOT IN (subquery)`; use `NOT EXISTS`.

## Migrations
- Alembic only, one head (`alembic-single-head` hook). Data moves go in the revision, never a separate script.
- Old code never sees the new schema (the app is stopped first), so a revision may drop or rename in one step.
- Every revision runs on the prod snapshot before it ships: `tests/migrations/` upgrade, downgrade, upgrade; then the flow on dev with `scripts/prod_to_dev.sh`.
- Never touch prod data without the owner's word.
