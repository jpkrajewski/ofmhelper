# db

One package per table (`<table>/repository.py`, `store.py`, `schema.py`); only the repositories and stores touch the database (`.claude/rules/database.md`); the rest of what this folder holds is described in `web/CLAUDE.md` "db/".

## A cache that reads another table (2026-11-20)
- `CachedRepository.cache_depends_on` names the namespaces its reads come from: `DriveFolderRepository` (`drive_folder`) reads phone logins, so it depends on `device`, and `DeviceRepository.logins_changed()` bumps `device`. The store that changes rows bumps its own table only; it never imports another table's store to clear its cache (that made `db.devices` and `db.drive_folders` import each other).

## Who did what: `audit_log` and the row stamps (2026-11-15)
Owner's backlog: "who changed this and from what", "can an AI agent search user actions with ease?".

- **Recorded by Postgres, never by the app.** ~100 Core `insert()`/`update()`/`delete()` statements skip ORM events, so an ORM hook would miss most writes. Triggers on each audited table do it, like `phone_revisions`:
  - `<table>_stamp` (BEFORE INSERT OR UPDATE): sets `updated_at = now()` and `updated_by` only when a column that is not quiet changed or the write set `updated_at` itself (a phone "touched" by its login: `devices/repository._touch`). Otherwise the old stamps stay. `created_at`/`created_by` never change.
  - `<table>_audit` (AFTER INSERT OR UPDATE OR DELETE): an UPDATE stores `{column: [old, new]}` of the changed columns and writes no row when only quiet columns changed. A masked column is stored only as `"set"` / `"changed"` (passwords, tokens, API keys, cookies, a Telegram chat id). `owner` is a column, or `table.column` to look the owner up (a phone link: its phone's owner, else its login's).
  - `audit_log` is append-only: a guard refuses UPDATE, DELETE and TRUNCATE for every client. It has no foreign keys, so it outlives rows and people.
- **Audited tables:** `db/audit.py` `AUDITED` (the rows people edit); `jobs` for `hidden_at` only (who hid a card), `AUDITED_COLUMNS`. Every other table is in `EXCLUDED` with its reason. **New table:** add one line in one of the two lists; `tests/integration/db/test_audit.py` reads `pg_trigger` and fails on a table in neither.
- **Who acts** (`ofmhelpers/acting.py`, top level: web, features, the worker and the sweeps set it, `db/session.py` reads it): `Acting` is kept in a ContextVar and set by `AuthMiddleware.dispatch`, `run_job`, each sweep's `_loop`, `worker.main` and `alembic/env.py`. `viewing_id` = whom an admin views as, or a manager's employer. `db/session.py`'s engine listener sends it with `set_config('ofm.*', ..., true)` on the first INSERT/UPDATE/DELETE of a transaction, on the same cursor, with no span of its own. A read-only transaction pays nothing. Nobody set (psql, a script): the log says `via = sql:<application_name>`.
- **Stamps** (`db/models.py` `Timestamps` + `Authored`): `created_at`/`updated_at` timestamptz, `created_by`/`updated_by` FK users ON DELETE SET NULL.
  - Pydantic fields stay `float`; repositories map with `.timestamp()`.
  - A repository never writes these stamps; the database does. A test that backdates one turns the trigger off for its transaction: `SET LOCAL session_replication_role = replica`.
  - Still floats (system-written, read as numbers): `users.last_login_at`, `devices.retired_at`, `logins.archived_at`, `tracked_accounts` scan times.
- **Phone history:** stored revisions are never rewritten. `phone_snapshot_current(snapshot)` reads one from before (floats, `added_at`) in today's shape, and `phone_revisions/repository._put_back` restores through it.
- **Reading the log:**
  - CLI in the app container: `python -m ofmhelpers.features.audit row devices 42 | actor 7 | owner 7 [--limit N]` (JSON lines). Each entry's `trace_id` opens the request or job in Logfire (the `logfire` skill's one-trace recipe).
  - SQL: `SELECT created_at, op, actor_id, viewing_id, via, ref, changes FROM audit_log WHERE table_name = 'devices' AND row_id = '42' ORDER BY id DESC`. A multi-column key is joined with ":" (`user_api_keys`: `7:kie`).
  - View as start / end are `viewing` / `viewing_end` events in the action log (`log_event`).
- **Not done:** retention (the log only grows; small at today's edit volume), versioning / optimistic locking, public UUIDs, soft delete unified (each table keeps its own: `retired_at`, `archived_at`, `untracked_at`, `removed_at`). Owner decision 2026-11-15.
- **SQLModel: not used** (owner 2026-11-15). It would be a new dependency, SQLAlchemy is pinned `<2.1` for Logfire, the models use an encrypted TypeDecorator, deferred columns, `column_property`, partial indexes and Core upserts, and rows often differ from their models (`Job.actor`, `Device` with its logins), so the converters would stay.

## Layout findings (review 2026-11-15, nothing dropped: owner decision)
- **Unused:**
  - `watched_profile_scans` is written and never read.
  - `drive_day_folders` is still there, unmapped: it holds rows (`alembic/env.py`).
  - The retired pages' tables stay: /reels `watched_profile*`, /clone-links `clone_from_links` + `clone_ai_checks`, /pictures `picture_presets`.
- **Duplicates:**
  - `drive_uploads` and `clone_from_links` both record a clone going to Drive.
  - Big sales go to both `chat_sales` and `chat_notifications`.
  - `user_id` is copied onto a job's children (downloads, drive_uploads, clone_*).
  - The latest scan is kept as JSONB next to its history table (`tracked_accounts.metrics` vs snapshots).
- **Names:**
  - The owner column is mixed: `self_link_domains.owner_id`, the text `devices.owner` (a phone's person label), `user_id` elsewhere.
  - Redundant `user_id` indexes sit on PKs that start with `user_id` (`watched_profiles`, `watched_profile_groups`).
  - `drive_uploads.job_id` has no FK.
