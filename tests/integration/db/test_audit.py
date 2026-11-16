"""audit_log and the row stamps (alembic a1b2c3d4e5f8): every change to a row
people edit is recorded by the database itself, with who did it (and whom
an admin was viewing as), from where (a request, a job, a sweep) and what
changed; secrets only as "changed"; nobody can edit the log. Owner
2026-11-15: "who changed this and from what", "can an AI agent search user
actions"."""

import asyncio
import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from signin import make_user, sign_in
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from ofmhelpers.db import audit
from ofmhelpers.db.acting import Acting, Via, acting
from ofmhelpers.db.models import Authored, Base, Timestamps
from ofmhelpers.db.session import get_engine, session_scope
from ofmhelpers.db.stores import audit_log, devices, phone_revisions
from ofmhelpers.db.stores import users as user_store
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.jobs import runner, sweeps
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app

OWNER = "owner@example.com"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())


@pytest.fixture
def owner() -> int:
    return make_user(OWNER).id


def _phone(owner: int, label: str = "Phone 01") -> int:
    return devices.add_device(DeviceFields(label=label), owner)


def _changes(table: str, row_id: object) -> list:
    return list(reversed(audit_log.history(table, str(row_id))))


# --- who acts ------------------------------------------------------------------


def test_a_request_records_the_person_and_what_they_sent(owner):
    job_id = create_job("ai", {}, actor=str(owner))
    client = TestClient(app)
    sign_in(client, OWNER)

    client.post(f"/jobs/{job_id}/hidden", json={"hidden": True})

    (entry,) = _changes("jobs", job_id)
    assert (entry.op, entry.actor_id, entry.owner_id) == ("UPDATE", owner, owner)
    assert (entry.via, entry.ref) == ("request", f"POST /jobs/{job_id}/hidden")
    assert entry.viewing_id is None
    old, new = entry.changes["hidden_at"]
    assert old is None
    assert new is not None
    assert list(entry.changes) == ["hidden_at"]  # nothing else changed


def test_an_admin_viewing_as_someone_is_recorded_with_whom(owner):
    admin = make_user("boss@example.com").id
    with acting(Acting(via=Via.REQUEST, actor_id=admin, viewing_id=owner)):
        phone = _phone(owner)

    (entry,) = _changes("devices", phone)
    assert (entry.actor_id, entry.viewing_id, entry.owner_id) == (admin, owner, owner)
    assert entry.op == "INSERT"
    assert entry.changes["label"] == "Phone 01"


def test_a_job_records_whose_it_is_and_which_job(owner):
    job_id = create_job("trials", {}, actor=str(owner))

    def work() -> None:
        _phone(owner, "From a job")

    runner.run_job(job_id, work, {})

    job = get_job(job_id)
    assert job is not None
    assert job.status == "done"
    phone = next(d for d in devices.list_devices(owner) if d.label == "From a job")
    (entry,) = _changes("devices", phone.id)
    assert (entry.via, entry.ref, entry.actor_id) == (
        "job",
        f"trials {job_id}",
        owner,
    )


def test_a_sweep_is_the_system(owner):
    made: list[int] = []
    once = sweeps.Sweep(name="test sweep", run=lambda: made.append(_phone(owner)))

    asyncio.run(sweeps._loop(once))

    (entry,) = _changes("devices", made[0])
    assert (entry.via, entry.ref, entry.actor_id) == ("sweep", "test sweep", None)


def test_a_client_of_its_own_is_named(owner):
    phone = _phone(owner, "Second")
    with session_scope() as s:
        s.execute(text("SET LOCAL application_name = 'psql'"))
        s.execute(
            text("UPDATE devices SET notes = 'by hand' WHERE id = :id"), {"id": phone}
        )

    entry = _changes("devices", phone)[-1]
    assert (entry.via, entry.actor_id) == ("sql:psql", None)
    assert entry.changes == {"notes": ["", "by hand"]}


# --- what is recorded ------------------------------------------------------------


def test_secrets_are_recorded_only_as_set_or_changed(owner):
    phone = _phone(owner)
    with acting(Acting(via=Via.REQUEST, actor_id=owner)):
        added = devices.add_login(
            phone,
            LoginFields(service="instagram", username="ann.ig", password="hunter2"),
            owner,
        )
        assert added is not None
        devices.update_login(
            added.id,
            LoginFields(service="instagram", username="ann.ig", password="hunter3"),
            owner,
        )

    made, changed = _changes("logins", added.id)
    assert made.changes["password"] == "set"
    assert changed.changes == {"password": "changed"}
    everything = text("SELECT changes::text FROM audit_log")
    with session_scope() as s:
        logged = " ".join(s.execute(everything).scalars())
    assert "hunter" not in logged


def test_a_change_to_quiet_columns_only_is_not_recorded_and_keeps_the_stamps(owner):
    before = user_store.get_user(owner)
    with session_scope() as s:
        stamps = s.execute(
            text("SELECT updated_at, updated_by FROM users WHERE id = :id"),
            {"id": owner},
        ).one()
        s.execute(
            text("UPDATE users SET last_seen_at = now() WHERE id = :id"), {"id": owner}
        )
    with session_scope() as s:
        after = s.execute(
            text("SELECT updated_at, updated_by FROM users WHERE id = :id"),
            {"id": owner},
        ).one()

    assert after == stamps
    assert [e.op for e in _changes("users", owner)] == ["INSERT"]
    assert before is not None


def test_rows_carry_who_made_and_changed_them(owner):
    other = make_user("va@example.com").id
    with acting(Acting(via=Via.REQUEST, actor_id=owner)):
        phone = _phone(owner)
    with session_scope() as s:
        made = s.execute(
            text(
                "SELECT created_by, updated_by, created_at, updated_at FROM devices WHERE id = :id"
            ),
            {"id": phone},
        ).one()
    with acting(Acting(via=Via.REQUEST, actor_id=other)):
        devices.update_device(
            phone, DeviceFields(label="Phone 01", notes="moved"), owner
        )
    with session_scope() as s:
        changed = s.execute(
            text(
                "SELECT created_by, updated_by, created_at, updated_at FROM devices WHERE id = :id"
            ),
            {"id": phone},
        ).one()

    assert (made.created_by, made.updated_by) == (owner, owner)
    assert (changed.created_by, changed.updated_by) == (owner, other)
    assert changed.created_at == made.created_at
    assert changed.updated_at > made.updated_at


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET ref = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
def test_nobody_can_change_the_log(owner, statement):
    _phone(owner)

    with pytest.raises(DBAPIError, match="append-only"), session_scope() as s:
        s.execute(text(statement))


# --- every table decided ------------------------------------------------------------


def _triggers() -> set[str]:
    with get_engine().connect() as conn:
        return set(
            conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            ).scalars()
        )


def test_every_table_is_audited_or_says_why_not():
    tables = set(inspect(get_engine()).get_table_names()) & set(Base.metadata.tables)
    decided = audit.AUDITED | set(audit.AUDITED_COLUMNS) | set(audit.EXCLUDED)

    assert sorted(tables - decided) == []
    assert sorted(audit.AUDITED & set(audit.EXCLUDED)) == []
    triggers = _triggers()
    for table in audit.AUDITED:
        assert f"{table}_audit" in triggers, table
        assert f"{table}_stamp" in triggers, table
    for table in audit.AUDITED_COLUMNS:
        assert f"{table}_audit" in triggers, table
    for table in audit.EXCLUDED:
        assert f"{table}_audit" not in triggers, table


def test_every_audited_table_has_both_stamps():
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls.__tablename__ in audit.AUDITED:
            assert issubclass(cls, Timestamps), cls.__name__
            assert issubclass(cls, Authored), cls.__name__


# --- phone history from before the stamps --------------------------------------------


def test_a_phone_revision_from_before_the_stamps_restores(owner):
    """Stored revisions are never rewritten: one in the old shape (floats,
    `added_at` / `linked_at`) is read in today's shape when restored."""
    phone = _phone(owner)
    added = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig"), owner
    )
    assert added is not None
    old_shape = text(
        """
        INSERT INTO phone_revisions (user_id, txid, change, snapshot)
        VALUES (:o, txid_current(), 'old shape',
        jsonb_build_object(
            'devices', (SELECT jsonb_agg(
                (to_jsonb(d) - 'created_at' - 'updated_at' - 'created_by' - 'updated_by')
                || jsonb_build_object('added_at', 1700000000.5, 'updated_at', 1700000001.5))
                FROM devices d WHERE d.user_id = :o),
            'logins', (SELECT jsonb_agg(
                (to_jsonb(l) - 'created_at' - 'updated_at' - 'created_by' - 'updated_by')
                || jsonb_build_object('added_at', 1700000000.5, 'updated_at', 1700000001.5))
                FROM logins l WHERE l.user_id = :o),
            'device_logins', (SELECT jsonb_agg(
                (to_jsonb(dl) - 'created_at' - 'updated_at' - 'created_by' - 'updated_by')
                || jsonb_build_object('linked_at', 1700000000.5))
                FROM device_logins dl JOIN devices d ON d.id = dl.device_id
                WHERE d.user_id = :o)))
        RETURNING id
        """
    )
    with session_scope() as s:
        revision = s.execute(old_shape, {"o": owner}).scalar_one()
    devices.delete_device(phone, owner)
    assert devices.list_devices(owner) == []

    done = phone_revisions.restore(revision, owner)

    assert done is not None
    (back,) = devices.list_devices(owner)
    assert back.id == phone
    assert back.added_at == 1700000000.5
    assert [x.username for x in back.logins] == ["ann.ig"]
