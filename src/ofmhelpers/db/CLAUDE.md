# db

Only `repositories/` and `stores/` touch the database (`.claude/rules/database.md`); the rest of what this folder holds is described in `web/CLAUDE.md` "db/".

## Who did what: `audit_log` and the row stamps (2026-11-15, alembic a1b2c3d4e5f8)
Owner's backlog: "who changed this and from what", "can an AI agent search user actions with ease?".

- **Recorded by Postgres, never by the app.** ~100 Core `insert()`/`update()`/`delete()` statements skip ORM events, so an ORM hook would miss most writes. Triggers on each audited table do it, like `phone_revisions` (b7c8d9e0f1a3):
  - `<table>_stamp`: `stamp_row(quiet)`, BEFORE INSERT OR UPDATE. On insert it sets `created_by`/`updated_by` to the actor. On update it sets `updated_at = now()` and `updated_by` = the actor, but only when a column that is not quiet changed or the write set `updated_at` itself (a phone "touched" by its login: `repositories/devices._touch`). Otherwise the old stamps stay. `created_at`/`created_by` never change.
  - `<table>_audit`: `audit_row(quiet, masked, keys, owner)`, AFTER INSERT OR UPDATE OR DELETE.
    - One `audit_log` row per change. An UPDATE stores `{column: [old, new]}` of the changed columns, and writes no row when only quiet columns changed. An INSERT or DELETE stores the whole row.
    - A masked column is stored only as `"set"` / `"changed"`. Masked: passwords, tokens, API keys, cookies, a Telegram chat id.
    - `owner` is a column, or `table.column` to look the owner up (a phone link: its phone's owner, else its login's).
  - `audit_log` is append-only: a guard refuses UPDATE, DELETE and TRUNCATE for every client. It has no foreign keys, so it outlives rows and people.
- **Audited tables:** `db/audit.py` `AUDITED`. They are the rows people edit: users, user_identities, user_api_keys, user_cookies, devices, logins, device_logins, tracked_accounts, creators, watched_profile_groups, short_links, picture_presets, self_link_domains, clone_examples, user_event_targets.
- **`jobs`:** audited for `hidden_at` only (who hid a card), `AUDITED_COLUMNS`.
- **Every other table:** in `EXCLUDED` with its reason.
- **New table:** add one line in one of the two lists. `tests/integration/db/test_audit.py` reads `pg_trigger` and fails on a table in neither.
- **Who acts** (`db/acting.py`):
  - `Acting` holds `via` (request | job | sweep | worker | migration | telegram), `actor_id`, `viewing_id` (whom an admin views as, or a manager's employer), `ref` ("POST /phones/3", "trials 1a2b3c4d") and `trace_id`. It is kept in a ContextVar.
  - Set by:
    - `AuthMiddleware.dispatch`: the person signed in, plus `viewer.by`.
    - `run_job`: the job's person, inside its span.
    - each sweep's `_loop`.
    - `worker.main`.
    - `alembic/env.py` (session-level).
  - `db/session.py`'s engine listener sends it with `set_config('ofm.*', ..., true)` on the first INSERT/UPDATE/DELETE of a transaction, on the same cursor, with no span of its own. A read-only transaction pays nothing.
  - Nobody set (psql, a script): the log says `via = sql:<application_name>`.
- **Stamps** (`db/models.py` `Timestamps` + `Authored`): `created_at`/`updated_at` timestamptz, default now(); `created_by`/`updated_by` FK users ON DELETE SET NULL.
  - The float epochs of those tables became timestamptz (`to_timestamp`, kept to 1 µs: snapshot test), and one name per fact: `added_at`/`linked_at` became `created_at`, `user_cookies.uploaded_at` became `updated_at`.
  - Pydantic fields stay `float`; repositories map with `.timestamp()`.
  - A repository never writes these stamps; the database does. A test that backdates one turns the trigger off for its transaction: `SET LOCAL session_replication_role = replica` (`tests/integration/web/test_links.py`).
  - Still floats (system-written, read as numbers): `users.last_login_at`, `devices.retired_at`, `logins.archived_at`, `tracked_accounts` scan times.
- **Phone history:** stored revisions are never rewritten. `phone_snapshot_current(snapshot)` reads one from before (floats, `added_at`) in today's shape, and `repositories/phone_revisions._put_back` restores through it.
- **Reading the log:**
  - `repositories/audit_log.py` / `stores/audit_log.py`: `history(table, row_id)`, `by_actor(id)`, `of_owner(id)`, newest first.
  - CLI in the app container: `python -m ofmhelpers.features.audit row devices 42 | actor 7 | owner 7 [--limit N]` (JSON lines). Each entry's `trace_id` opens the request or job in Logfire (the `logfire` skill's one-trace recipe).
  - SQL: `SELECT created_at, op, actor_id, viewing_id, via, ref, changes FROM audit_log WHERE table_name = 'devices' AND row_id = '42' ORDER BY id DESC`. A multi-column key is joined with ":" (`user_api_keys`: `7:kie`).
  - View as start / end are `viewing` / `viewing_end` events in the action log (`log_event`).
- **Not done:** retention (the log only grows; small at today's edit volume), versioning / optimistic locking, public UUIDs, soft delete unified (each table keeps its own: `retired_at`, `archived_at`, `untracked_at`, `removed_at`). Owner decision 2026-11-15.
- **SQLModel: not used** (owner 2026-11-15). It would be a new dependency, SQLAlchemy is pinned `<2.1` for Logfire, the models use an encrypted TypeDecorator, deferred columns, `column_property`, partial indexes and Core upserts, and rows often differ from their models (`Job.actor`, `Device` with its logins), so the converters would stay.

## Layout findings (review 2026-11-15, nothing dropped: owner decision)
- **Unused:**
  - `watched_profile_scans` is written and never read.
  - `drive_day_folders` is retired.
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
