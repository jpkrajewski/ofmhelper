"""Alembic migrations against a snapshot of the production database.

`tests/fixtures/prod_snapshot.sql` is a plain `pg_dump` of production (real
customer data: gitignored, never committed -- the repo is public). Refresh
it before testing a migration (see CLAUDE.local.md); without it, these tests
skip, as on CI. Each test loads it into its own throwaway database at the
revision production is on, then runs the real migration files through
`alembic.command`, exactly as the `migrate` service does on deploy.

The round-trip test holds for any revision. A revision's own tests (`needs`)
run while the snapshot predates it and skip once production has it -- add
them for every new revision, and refresh the snapshot first.
"""

import os
import subprocess
from collections.abc import Iterator

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel
from repo_paths import FIXTURES, REPO
from sqlalchemy import Engine, create_engine, func, inspect, select, table, text
from sqlalchemy.engine import URL, make_url

from alembic import command

SNAPSHOT = FIXTURES / "prod_snapshot.sql"

# Opt-in (`scripts/test.sh snapshot`): real customer data, minutes per run.
# The empty-database round trip runs every time (test_migrations.py).
pytestmark = [
    pytest.mark.snapshot,
    pytest.mark.skipif(
        not SNAPSHOT.exists(), reason="no production snapshot (tests/fixtures)"
    ),
]


class StoredPost(BaseModel, frozen=True):
    account_id: int
    post_id: str
    views: int
    posted_at: float


def _alembic() -> Config:
    return Config(str(REPO / "alembic.ini"))


@pytest.fixture
def snapshot_url(monkeypatch, test_database_url) -> Iterator[URL]:
    """A fresh database holding the snapshot; Alembic and the app's settings
    point at it."""
    # Named after the test database: one per xdist worker.
    base = make_url(test_database_url)
    url = base.set(database=f"{base.database}_snapshot")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    libpq = url.set(drivername="postgresql").render_as_string(hide_password=False)
    subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", libpq, "-f", str(SNAPSHOT)],
        check=True,
        capture_output=True,
        env={**os.environ, "PGOPTIONS": "-c client_min_messages=warning"},
    )
    monkeypatch.setenv("OFM_DATABASE_URL", url.render_as_string(hide_password=False))
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def db(snapshot_url) -> Iterator[Engine]:
    engine = create_engine(snapshot_url)
    yield engine
    engine.dispose()


def _revision(db: Engine) -> str:
    with db.connect() as conn:
        return conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _columns(db: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(db).get_columns(table)}


def _count(db: Engine, name: str) -> int:
    with db.connect() as conn:
        return conn.execute(select(func.count()).select_from(table(name))).scalar_one()


def _json_posts(db: Engine) -> set[StoredPost]:
    """The posts as the old schema keeps them: each account's JSONB list."""
    with db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, p ->> 'id', (p ->> 'views')::bigint,
                       (p ->> 'posted_at')::double precision
                FROM tracked_accounts AS a, jsonb_array_elements(a.posts) AS p
                WHERE a.scanned_at IS NOT NULL
                """
            )
        ).all()
    return {
        StoredPost(account_id=a, post_id=i, views=v, posted_at=t) for a, i, v, t in rows
    }


def _table_posts(db: Engine) -> set[StoredPost]:
    with db.connect() as conn:
        rows = conn.execute(
            text("SELECT account_id, post_id, views, posted_at FROM tracked_posts")
        ).all()
    return {
        StoredPost(account_id=a, post_id=i, views=v, posted_at=t) for a, i, v, t in rows
    }


def _fully_covered(db: Engine) -> dict[int, bool]:
    """Per scanned account, whether every view window it stored was covered."""
    with db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT a.id, NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(a.metrics -> 'windows') AS w
                    WHERE NOT (w ->> 'covered')::boolean)
                FROM tracked_accounts AS a WHERE a.metrics IS NOT NULL
                """
            )
        ).all()
    return dict(rows)


# --- every revision, whatever the snapshot is on ----------------------------


def _pending(db: Engine, revision: str) -> bool:
    """Whether `revision` is still to be applied on top of the snapshot."""
    script = ScriptDirectory.from_config(_alembic())
    ahead = script.iterate_revisions("head", _revision(db))
    return revision in {r.revision for r in ahead}


def needs(revision: str):
    """Runs a revision's own test only while the snapshot predates it: once
    production has it, a fresh snapshot has nothing left for it to move."""

    def check(db: Engine) -> None:
        if not _pending(db, revision):
            pytest.skip(f"{revision} is already applied on production")

    return check


def _row_counts(db: Engine) -> dict[str, int]:
    return {t: _count(db, t) for t in inspect(db).get_table_names()}


# Tables a revision deletes rows from on purpose, for good (a downgrade
# cannot bring them back): d4e5f6a7b8c0 drops the bots' spam leads.
_DELETES_FOR_GOOD = {"applications"}


def test_upgrade_downgrade_upgrade_keeps_every_row(db):
    """Up to head, back to production's revision, up again: every table
    production has keeps its rows (but those of `_DELETES_FOR_GOOD`), and
    head is reached twice the same way."""

    def kept(counts: dict[str, int]) -> dict[str, int]:
        return {t: n for t, n in counts.items() if t not in _DELETES_FOR_GOOD}

    start = _revision(db)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    at_head = _row_counts(db)
    command.downgrade(_alembic(), start)
    assert _revision(db) == start
    assert kept(_row_counts(db)) == kept(before)
    command.upgrade(_alembic(), "head")
    assert _row_counts(db) == at_head


# --- per revision -------------------------------------------------------------

TRACKED_POSTS = "f7a8b9c0d1e2"
UNTRACKED = "a9b0c1d2e3f4"
IDENTITY = "b0c1d2e3f4a5"
DEVICES = "c1d2e3f4a5b6"
DRIVE = "d2e3f4a5b6c7"
POSTED = "a5b6c7d8e9f0"
FINISHED_AT = "b6c7d8e9f0a1"
USERS = "c2d3e4f5a6b7"
OWNERS = "d3e4f5a6b7c8"
SPAM = "d4e5f6a7b8c0"
HIDDEN = "f8a9b0c1d2e3"
REEL_CHECKS = "a2b3c4d5e6f7"
PICTURE_PRESETS = "9c1d2e3f4a5b"
PRESET_ASPECT = "4e5f6a7b8c9d"


def test_tracked_posts_moves_every_post_out_of_the_jsonb(db):
    needs(TRACKED_POSTS)(db)
    posts = _json_posts(db)
    covered = _fully_covered(db)
    command.upgrade(_alembic(), TRACKED_POSTS)

    assert _table_posts(db) == posts
    assert "posts" not in _columns(db, "tracked_accounts")
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, covered_since, metrics ? 'windows' FROM tracked_accounts"
                " WHERE metrics IS NOT NULL"
            )
        ).all()
    oldest: dict[int, float] = {}
    for p in posts:
        oldest[p.account_id] = min(oldest.get(p.account_id, p.posted_at), p.posted_at)
    for account_id, covered_since, has_windows in rows:
        assert not has_windows
        expected = 0 if covered[account_id] else oldest.get(account_id)
        assert covered_since == expected, account_id


def test_tracked_posts_downgrade_puts_the_posts_back(db):
    needs(TRACKED_POSTS)(db)
    start, posts = _revision(db), _json_posts(db)
    command.upgrade(_alembic(), TRACKED_POSTS)
    command.downgrade(_alembic(), start)
    assert _json_posts(db) == posts
    assert "covered_since" not in _columns(db, "tracked_accounts")
    assert not inspect(db).has_table("tracked_posts")


def test_untracked_keeps_every_account_tracked(db):
    needs(UNTRACKED)(db)
    command.upgrade(_alembic(), UNTRACKED)
    with db.connect() as conn:
        untracked = conn.execute(
            text("SELECT count(*) FROM tracked_accounts WHERE untracked_at IS NOT NULL")
        ).scalar_one()
    assert untracked == 0


def test_untracked_downgrade_deletes_no_account(db):
    needs(UNTRACKED)(db)
    command.upgrade(_alembic(), UNTRACKED)
    accounts = _count(db, "tracked_accounts")
    with db.begin() as conn:
        conn.execute(text("UPDATE tracked_accounts SET untracked_at = 1"))
    command.downgrade(_alembic(), TRACKED_POSTS)
    assert _count(db, "tracked_accounts") == accounts


def test_identity_starts_unknown_with_no_failures(db):
    needs(IDENTITY)(db)
    command.upgrade(_alembic(), IDENTITY)
    with db.connect() as conn:
        ids, failures = conn.execute(
            text(
                "SELECT count(platform_account_id), coalesce(sum(failed_scans), 0)"
                " FROM tracked_accounts"
            )
        ).one()
    assert (ids, failures) == (0, 0)


def test_devices_adds_empty_tables_and_drops_only_them(db):
    needs(DEVICES)(db)
    command.upgrade(_alembic(), IDENTITY)
    before = _row_counts(db)
    command.upgrade(_alembic(), DEVICES)
    for name in ("devices", "logins", "device_logins"):
        assert _count(db, name) == 0
    assert {"password", "backup_codes"} <= _columns(db, "logins")
    with db.begin() as conn:
        device = conn.execute(
            text(
                "INSERT INTO devices (label, added_at, updated_at)"
                " VALUES ('p1', 1, 1) RETURNING id"
            )
        ).scalar_one()
        login = conn.execute(
            text(
                "INSERT INTO logins (service, username, password, added_at, updated_at)"
                " VALUES ('gmail', 'a@b.c', 'tok', 1, 1) RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO device_logins (device_id, login_id, linked_at)"
                " VALUES (:d, :l, 1)"
            ),
            {"d": device, "l": login},
        )
    command.downgrade(_alembic(), IDENTITY)
    assert _row_counts(db) == before


def test_drive_folders_follow_their_login_and_drop_only_themselves(db):
    needs(DRIVE)(db)
    command.upgrade(_alembic(), DEVICES)
    before = _row_counts(db)
    command.upgrade(_alembic(), DRIVE)
    with db.begin() as conn:
        login = conn.execute(
            text(
                "INSERT INTO logins (service, username, added_at, updated_at)"
                " VALUES ('instagram', 'drive_probe', 1, 1) RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO drive_account_folders (login_id, folder_id, name,"
                " updated_at) VALUES (:l, 'f1', 'drive_probe - Ann', 1)"
            ),
            {"l": login},
        )
        conn.execute(
            text(
                "INSERT INTO drive_day_folders (login_id, day, folder_id, created_at)"
                " VALUES (:l, '2026-10-21', 'f2', 1)"
            ),
            {"l": login},
        )
        conn.execute(text("DELETE FROM logins WHERE id = :l"), {"l": login})
    assert _count(db, "drive_account_folders") == 0
    assert _count(db, "drive_day_folders") == 0
    command.downgrade(_alembic(), DEVICES)
    assert _row_counts(db) == before


def test_posted_folders_keep_every_account_folder_and_day_folder(db):
    """Every account folder keeps its row with no POSTED id yet (the first
    sweep finds or makes one); the day folders' rows stay for a rollback."""
    needs(POSTED)(db)
    command.upgrade(_alembic(), "f4a5b6c7d8e9")
    with db.connect() as conn:
        accounts = conn.execute(
            text("SELECT login_id, folder_id, name FROM drive_account_folders")
        ).all()
    days = _count(db, "drive_day_folders")

    command.upgrade(_alembic(), POSTED)

    with db.connect() as conn:
        after = conn.execute(
            text(
                "SELECT login_id, folder_id, name, posted_folder_id"
                " FROM drive_account_folders"
            )
        ).all()
    assert {(a, f, n) for a, f, n, _ in after} == set(accounts)
    assert all(p is None for *_, p in after)
    assert _count(db, "drive_day_folders") == days
    assert _count(db, "drive_posted_files") == 0


def test_finished_at_keeps_every_job_untimed(db):
    """Old jobs never recorded a finish time: every row stays, with NULL
    (no duration, so no estimate from them)."""
    needs(FINISHED_AT)(db)
    command.upgrade(_alembic(), POSTED)
    jobs = _count(db, "jobs")

    command.upgrade(_alembic(), FINISHED_AT)

    with db.connect() as conn:
        timed = conn.execute(
            text("SELECT count(*) FROM jobs WHERE finished_at IS NOT NULL")
        ).scalar()
    assert _count(db, "jobs") == jobs
    assert timed == 0


def test_a_revision_on_production_is_no_longer_pending(db):
    # What `needs` relies on: once the snapshot carries a revision, its own
    # tests skip instead of failing on data it already moved.
    command.upgrade(_alembic(), "head")
    head = _revision(db)
    assert not _pending(db, head)
    assert not _pending(db, TRACKED_POSTS)


def _rows(db: Engine, sql: str) -> list:
    with db.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def _scalar(db: Engine, sql: str) -> object:
    with db.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def test_users_gives_every_admin_and_va_job_to_the_password_admin(db):
    needs(USERS)(db)
    roles = dict(_rows(db, "SELECT actor, count(*) FROM jobs GROUP BY actor"))
    command.upgrade(_alembic(), USERS)
    admin_id = _scalar(db, "SELECT id FROM users WHERE email = 'admin'")
    after = dict(_rows(db, "SELECT actor, count(*) FROM jobs GROUP BY actor"))
    assert _scalar(db, "SELECT role FROM users WHERE email = 'admin'") == "admin"
    assert after.get(str(admin_id), 0) == roles.get("admin", 0) + roles.get("va", 0)
    assert "va" not in after
    assert "admin" not in after
    assert after.get(None) == roles.get(None)


def test_owners_gives_every_row_to_the_password_admin_and_keeps_it(db):
    needs(OWNERS)(db)
    owned = (
        "devices",
        "logins",
        "tracked_accounts",
        "creators",
        "watched_profiles",
        "profile_groups",
    )
    before = {t: _count(db, t) for t in owned}
    command.upgrade(_alembic(), OWNERS)
    admin_id = _scalar(db, "SELECT id FROM users WHERE email = 'admin'")
    for name in owned:
        assert _count(db, name) == before[name], name
        assert (
            _scalar(db, f"SELECT count(*) FROM {name} WHERE user_id <> {admin_id}")  # noqa: S608 -- constants
            == 0
        ), name
    assert _scalar(db, "SELECT is_password_account FROM users WHERE email = 'admin'")
    assert "drive_root_id" in _columns(db, "users")


def test_spam_drops_only_the_bots_leads(db):
    """Production's leads: the bots' (a link in a field, a Telegram handle
    with spaces) go, every other lead stays as it was."""
    needs(SPAM)(db)
    spam = (
        r"SELECT id FROM applications WHERE telegram_handle ~ '\s'"
        " OR concat_ws(' ', first_name, last_name, email, phone, telegram_handle,"
        r" instagram_handle, goals) ~* '(https?://|www\.|\[/?url|<a\s|\[/?link)'"
    )
    with db.connect() as conn:
        everyone = set(conn.execute(text("SELECT id FROM applications")).scalars())
        bots = set(conn.execute(text(spam)).scalars())
    command.upgrade(_alembic(), "head")
    with db.connect() as conn:
        left = set(conn.execute(text("SELECT id FROM applications")).scalars())
    assert left == everyone - bots


def test_hidden_keeps_every_job_shown(db):
    """Production's jobs all stay, and none is hidden: every gallery looks
    as it did."""
    needs(HIDDEN)(db)
    jobs = _count(db, "jobs")
    command.upgrade(_alembic(), "head")
    assert _count(db, "jobs") == jobs
    assert _scalar(db, "SELECT count(*) FROM jobs WHERE hidden_at IS NOT NULL") == 0


def test_clone_ai_checks_adds_two_empty_tables_and_changes_no_row(db):
    """clone_ai_checks + clone_from_links are new and empty; every table production
    has keeps every row."""
    needs(REEL_CHECKS)(db)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    after = _row_counts(db)
    assert after["clone_ai_checks"] == 0
    assert after["clone_from_links"] == 0
    assert {t: n for t, n in after.items() if t in before} == before


def test_picture_presets_adds_an_empty_table_and_changes_no_row(db):
    """picture_presets is new and empty; every table production has keeps
    every row, and a downgrade drops only it."""
    needs(PICTURE_PRESETS)(db)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    after = _row_counts(db)
    assert after["picture_presets"] == 0
    assert {t: n for t, n in after.items() if t in before} == before
    command.downgrade(_alembic(), REEL_CHECKS)
    assert _row_counts(db) == before


def test_preset_aspect_adds_an_empty_column_and_changes_no_row(db):
    """picture_presets.aspect_ratio is new and NULL: every preset keeps
    following its pictures; a downgrade drops only the column."""
    needs(PRESET_ASPECT)(db)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    assert "aspect_ratio" in _columns(db, "picture_presets")
    assert (
        _scalar(
            db, "SELECT count(*) FROM picture_presets WHERE aspect_ratio IS NOT NULL"
        )
        == 0
    )
    assert {t: n for t, n in _row_counts(db).items() if t in before} == before
    command.downgrade(_alembic(), "-1")
    assert "aspect_ratio" not in _columns(db, "picture_presets")


CHAT_ACCOUNTS_SERVER = "c9b8a7f6e5d4"


def test_chat_accounts_remember_their_server_and_open_waits_get_an_index(db):
    needs(CHAT_ACCOUNTS_SERVER)(db)
    command.upgrade(_alembic(), CHAT_ACCOUNTS_SERVER)

    assert "connected_from" in _columns(db, "chat_accounts")
    indexes = {i["name"] for i in inspect(db).get_indexes("chat_fan_messages")}
    assert "ix_chat_fan_messages_open" in indexes


DAILY_REPORT = "e7f8a9b0c1d2"


def test_daily_report_tables_start_with_every_stored_sale(db):
    """chat_sales starts with the sales production kept for the pop-up; no report
    has run yet."""
    needs(DAILY_REPORT)(db)
    command.upgrade(_alembic(), CHAT_ACCOUNTS_SERVER)
    kept = _scalar(
        db,
        "SELECT count(*) FROM chat_notifications"
        " WHERE kind = 'sale' AND amount_cents IS NOT NULL",
    )

    command.upgrade(_alembic(), DAILY_REPORT)

    assert _scalar(db, "SELECT count(*) FROM chat_sales") == kept
    assert _scalar(db, "SELECT count(*) FROM report_runs") == 0


SELF_LINKS = "d5e6f7a8b9c0"


def test_self_links_adds_two_empty_tables_and_changes_no_row(db):
    """self_link_domains and self_link_clicks are new and empty; every table
    production has keeps every row, and a downgrade drops only the two."""
    needs(SELF_LINKS)(db)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    after = _row_counts(db)
    assert after["self_link_domains"] == 0
    assert after["self_link_clicks"] == 0
    indexes = {i["name"] for i in inspect(db).get_indexes("self_link_clicks")}
    assert "ix_self_link_clicks_link_ts" in indexes
    assert {t: n for t, n in after.items() if t in before} == before
    command.downgrade(_alembic(), DAILY_REPORT)
    assert _row_counts(db) == before


BRIDGE_INDEX = "e6f7a8b9c0d1"


def test_bridge_index_and_browser_column_change_no_row(db):
    """The bridge lookup index and self_link_clicks.browser are added without
    touching any row count; a downgrade drops only them."""
    needs(BRIDGE_INDEX)(db)
    command.upgrade(_alembic(), SELF_LINKS)
    before = _row_counts(db)
    command.upgrade(_alembic(), "head")
    assert "browser" in _columns(db, "self_link_clicks")
    indexes = {i["name"] for i in inspect(db).get_indexes("short_links")}
    assert "ix_short_links_bridge" in indexes
    assert _row_counts(db) == before
    command.downgrade(_alembic(), SELF_LINKS)
    assert "browser" not in _columns(db, "self_link_clicks")


JOB_OWNERS = "e3f4a5b6c7d9"
_BEFORE_JOB_OWNERS = "d2e3f4a5b6c8"

# A job's owner as the old schema says it: its actor when that is the id of
# a user who exists, else nobody.
_OWNERS_BY_ACTOR = (
    "SELECT j.id, u.id FROM jobs AS j LEFT JOIN users AS u"
    " ON j.actor ~ '^[0-9]+$' AND u.id::text = j.actor"
)


def test_job_owners_keep_every_job_with_its_owner_and_its_cost(db):
    """jobs.user_id: every job whose actor was an existing user's id keeps
    that owner, anything else is nobody's, no job is lost; a cost kept in
    meta is the cost_usd column and leaves meta, its generations stay."""
    needs(JOB_OWNERS)(db)
    command.upgrade(_alembic(), _BEFORE_JOB_OWNERS)
    jobs = _count(db, "jobs")
    owners = dict(_rows(db, _OWNERS_BY_ACTOR))
    costs = dict(
        _rows(
            db,
            "SELECT id, (meta ->> 'cost_usd')::numeric FROM jobs"
            " WHERE meta ->> 'cost_usd' IS NOT NULL",
        )
    )
    generations = dict(_rows(db, "SELECT id, meta -> 'generations' FROM jobs"))

    command.upgrade(_alembic(), JOB_OWNERS)

    assert _count(db, "jobs") == jobs
    assert "actor" not in _columns(db, "jobs")
    assert dict(_rows(db, "SELECT id, user_id FROM jobs")) == owners
    assert (
        dict(_rows(db, "SELECT id, cost_usd FROM jobs WHERE cost_usd IS NOT NULL"))
        == costs
    )
    assert _scalar(db, "SELECT count(*) FROM jobs WHERE meta ? 'cost_usd'") == 0
    assert _scalar(db, "SELECT count(*) FROM jobs WHERE cost_estimated") == 0
    assert dict(_rows(db, "SELECT id, meta -> 'generations' FROM jobs")) == generations
    indexes = {i["name"] for i in inspect(db).get_indexes("jobs")}
    assert "ix_jobs_user_id_created_at" in indexes
    (fk,) = [
        f
        for f in inspect(db).get_foreign_keys("jobs")
        if f["constrained_columns"] == ["user_id"]
    ]
    assert (fk["referred_table"], fk["options"].get("ondelete")) == (
        "users",
        "RESTRICT",
    )


DOWNLOADS = "f4b5c6d7e8a1"

# Every file of every done download job that is somebody's, as the job's
# JSONB result lists them: (job, the file's place among the job's files,
# link, path).
_DOWNLOADED_FILES = """
    SELECT j.id,
           (row_number() OVER (PARTITION BY j.id ORDER BY e.n, p.n) - 1)::int,
           e.entry ->> 'url',
           p.path
    FROM jobs AS j,
         jsonb_array_elements(j.result) WITH ORDINALITY AS e(entry, n),
         jsonb_array_elements_text(e.entry -> 'output_paths')
             WITH ORDINALITY AS p(path, n)
    WHERE j.task IN ('download_media', 'download_videos', 'download_images')
      AND j.status = 'done' AND j.user_id IS NOT NULL
      AND jsonb_typeof(j.result) = 'array'
      AND jsonb_typeof(j.result -> 0 -> 'output_paths') = 'array'
"""


def test_downloads_gets_one_row_per_file_of_every_download_job(db):
    """downloads: every output file of every done download job that has an
    owner is one row, at its place in the job, stored, last used when the
    job started, its store copy not known yet; no other table changes, and
    a downgrade drops only the new table."""
    needs(DOWNLOADS)(db)
    command.upgrade(_alembic(), JOB_OWNERS)
    before = _row_counts(db)
    files = set(_rows(db, _DOWNLOADED_FILES))

    command.upgrade(_alembic(), DOWNLOADS)

    assert set(_rows(db, "SELECT job_id, position, url, file_path FROM downloads")) == (
        files
    )
    assert _count(db, "downloads") == len(files)
    assert (
        _scalar(
            db,
            "SELECT count(*) FROM downloads AS d JOIN jobs AS j ON j.id = d.job_id"
            " WHERE d.user_id IS DISTINCT FROM j.user_id OR NOT d.stored"
            " OR d.asset_path IS NOT NULL OR d.removed_at IS NOT NULL"
            " OR d.created_at <> to_timestamp(j.created_at)"
            " OR d.last_used_at <> d.created_at",
        )
        == 0
    )
    assert {i["name"] for i in inspect(db).get_indexes("downloads")} >= {
        "ix_downloads_user_id_url",
        "ix_downloads_stored_last_used_at",
    }
    deletes = {
        f["referred_table"]: f["options"].get("ondelete")
        for f in inspect(db).get_foreign_keys("downloads")
    }
    assert deletes == {"users": "RESTRICT", "jobs": "CASCADE"}
    assert {t: n for t, n in _row_counts(db).items() if t != "downloads"} == before

    command.downgrade(_alembic(), JOB_OWNERS)

    assert "downloads" not in inspect(db).get_table_names()
    assert _row_counts(db) == before


def test_job_owners_downgrade_puts_actor_and_the_meta_cost_back(db):
    """Back on the old schema every job has the actor its owner was (a
    non-user actor became nobody's for good) and its meta as it was."""
    needs(JOB_OWNERS)(db)
    command.upgrade(_alembic(), _BEFORE_JOB_OWNERS)
    owners = {
        job_id: None if user_id is None else str(user_id)
        for job_id, user_id in _rows(db, _OWNERS_BY_ACTOR)
    }
    metas = dict(_rows(db, "SELECT id, meta FROM jobs"))

    command.upgrade(_alembic(), JOB_OWNERS)
    command.downgrade(_alembic(), _BEFORE_JOB_OWNERS)

    assert dict(_rows(db, "SELECT id, actor FROM jobs")) == owners
    assert dict(_rows(db, "SELECT id, meta FROM jobs")) == metas
    assert not {"user_id", "cost_usd", "cost_estimated"} & _columns(db, "jobs")
