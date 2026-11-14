"""/phones History: every change to a person's phones leaves their state,
recorded by the database itself, and any state can be gone back to (owner
2026-11-13: "someone removes a phone ... we can go back to any state").
Plus the daily encrypted backup file of those states."""

import os
from pathlib import Path

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from signin import make_user, sign_in
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import devices
from ofmhelpers.db.stores import phone_revisions as store
from ofmhelpers.features.phones import backup
from ofmhelpers.schemas import DeviceFields, LoginFields
from ofmhelpers.web.main import app

OWNER = "owner@example.com"
OTHER = "other@example.com"
PASSWORD = "s3cret-pass"
CODES = "1111 2222"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())


@pytest.fixture
def owner() -> int:
    return make_user(OWNER).id


@pytest.fixture
def client(owner) -> TestClient:
    c = TestClient(app)
    sign_in(c, OWNER)
    return c


def _phone(owner: int, label: str = "Phone 01") -> int:
    return devices.add_device(DeviceFields(label=label), owner)


def _login(device_id: int, owner: int, username: str = "danewalker") -> int:
    fields = LoginFields(
        service="instagram", username=username, password=PASSWORD, backup_codes=CODES
    )
    added = devices.add_login(device_id, fields, owner)
    assert added is not None
    return added.id


def _revisions(owner: int):
    return store.page(owner, 100)


def _labels(owner: int) -> list[str]:
    return sorted(d.label for d in devices.list_devices(owner))


def _usernames(owner: int) -> list[str]:
    return sorted(x.username for d in devices.list_devices(owner) for x in d.logins)


# --- the database records ------------------------------------------------------


def test_every_kind_of_change_leaves_exactly_one_revision(owner):
    steps = []
    phone = _phone(owner)
    steps.append(len(_revisions(owner)))
    login = _login(phone, owner)
    steps.append(len(_revisions(owner)))
    devices.update_device(phone, DeviceFields(label="Phone 02"), owner)
    steps.append(len(_revisions(owner)))
    devices.update_login(
        login, LoginFields(service="instagram", username="renamed"), owner
    )
    steps.append(len(_revisions(owner)))
    devices.retire_device(phone, owner)
    steps.append(len(_revisions(owner)))
    devices.restore_device(phone, owner)
    steps.append(len(_revisions(owner)))
    devices.archive_logins([login], owner)
    steps.append(len(_revisions(owner)))
    devices.restore_login(login, owner)
    steps.append(len(_revisions(owner)))
    devices.remove_login(phone, login, owner)
    steps.append(len(_revisions(owner)))
    devices.delete_device(phone, owner)
    steps.append(len(_revisions(owner)))

    assert steps == list(range(1, 11))
    newest = _revisions(owner)[0]
    assert (newest.phones, newest.accounts) == (0, 0)
    assert "devices DELETE" in newest.change


def test_one_transaction_names_every_table_it_touched_once(owner):
    """A link and then its login changed in one transaction: both named
    ("logins UPDATE" is also the end of "device_logins UPDATE")."""
    phone = _phone(owner)
    login = _login(phone, owner)

    with session_scope() as s:
        s.execute(text("UPDATE device_logins SET linked_at = linked_at"))
        s.execute(text("UPDATE logins SET notes = notes WHERE id = :id"), {"id": login})
        s.execute(text("UPDATE logins SET notes = notes WHERE id = :id"), {"id": login})

    assert _revisions(owner)[0].change == "device_logins UPDATE, logins UPDATE"


def test_a_revision_is_only_its_owners_and_holds_no_plain_secret(owner):
    other = make_user(OTHER).id
    _login(_phone(owner), owner)
    _phone(other, "Their phone")

    with session_scope() as s:
        stored = s.execute(
            text("SELECT snapshot::text FROM phone_revisions WHERE user_id = :u"),
            {"u": owner},
        ).scalars()
        snapshots = "".join(stored)

    assert [r.phones for r in _revisions(other)] == [1]
    assert "Their phone" not in snapshots
    assert "danewalker" in snapshots
    assert PASSWORD not in snapshots
    assert CODES not in snapshots


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE phone_revisions SET change = 'tampered'",
        "UPDATE phone_revisions SET snapshot = '{}'::jsonb",
        "DELETE FROM phone_revisions",
        "TRUNCATE phone_revisions",
    ],
)
def test_the_history_cannot_be_changed_or_deleted(owner, statement):
    _phone(owner)

    with pytest.raises(DBAPIError, match="append-only"), session_scope() as s:
        s.execute(text(statement))

    assert [r.change for r in _revisions(owner)] == ["devices INSERT"]


# --- going back ------------------------------------------------------------------


def test_a_deleted_phone_comes_back_with_its_accounts_and_passwords(owner):
    phone = _phone(owner)
    login = _login(phone, owner)
    before = _revisions(owner)[0].id
    devices.delete_device(phone, owner)
    assert _labels(owner) == []

    done = store.restore(before, owner)

    assert done is not None
    assert done.logins_back == [login]
    assert _labels(owner) == ["Phone 01"]
    assert _usernames(owner) == ["danewalker"]
    secrets = devices.secrets(login, owner)
    assert (secrets.password, secrets.backup_codes) == (PASSWORD, CODES)


def test_what_was_added_since_leaves_and_a_restore_can_be_undone(owner):
    first = _phone(owner)
    before = _revisions(owner)[0].id
    second = _phone(owner, "Phone 02")
    _login(second, owner)
    devices.update_device(first, DeviceFields(label="Renamed"), owner)
    after = _revisions(owner)[0].id

    store.restore(before, owner)
    went_back = (_labels(owner), _usernames(owner))
    store.restore(after, owner)

    assert went_back == (["Phone 01"], [])
    assert _labels(owner) == ["Phone 02", "Renamed"]
    assert _usernames(owner) == ["danewalker"]


def test_two_phones_that_swapped_labels_go_back(owner):
    a = _phone(owner, "A")
    b = _phone(owner, "B")
    before = _revisions(owner)[0].id
    devices.update_device(a, DeviceFields(label="tmp"), owner)
    devices.update_device(b, DeviceFields(label="A"), owner)
    devices.update_device(a, DeviceFields(label="B"), owner)

    store.restore(before, owner)

    by_id = {d.id: d.label for d in devices.list_devices(owner)}
    assert by_id == {a: "A", b: "B"}


def test_nobody_restores_another_persons_revision(owner):
    other = make_user(OTHER).id
    _phone(owner)
    theirs = _phone(other, "Theirs")
    revision = _revisions(other)[0].id
    devices.delete_device(theirs, other)

    assert store.restore(revision, owner) is None
    assert _labels(other) == []
    assert _labels(owner) == ["Phone 01"]


# --- the page ----------------------------------------------------------------------


def test_history_lists_the_changes_and_restore_goes_back(client, owner):
    phone = _phone(owner)
    _login(phone, owner)
    before = _revisions(owner)[0].id
    devices.delete_device(phone, owner)

    listed = client.get("/phones/revisions").text
    done = client.post(f"/phones/revisions/{before}/restoration")

    assert 'id="ph-history-open"' in client.get("/phones").text
    assert listed.count("ph-revision-restore") == 2
    assert "1 phone · 1 account" in listed
    assert PASSWORD not in listed
    assert done.status_code == 201
    assert done.json()["revision_id"] == before
    assert _labels(owner) == ["Phone 01"]


def test_restoring_a_revision_that_is_not_theirs_is_a_404(client):
    other = make_user(OTHER).id
    _phone(other, "Theirs")
    revision = _revisions(other)[0].id

    r = client.post(f"/phones/revisions/{revision}/restoration")

    assert (r.status_code, r.json()["detail"]) == (404, "No such revision")
    assert "Theirs" not in client.get("/phones/revisions").text


# --- the backup file ---------------------------------------------------------------


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "uploads_root", lambda: tmp_path)
    return tmp_path


def _lose_everything() -> None:
    """The database as after a disaster: no phone, no History (the users
    are still there)."""
    with session_scope() as s:
        s.execute(text("SET LOCAL session_replication_role = replica"))
        for name in ("device_logins", "logins", "devices", "phone_revisions"):
            s.execute(text(f"DELETE FROM {name}"))  # noqa: S608 -- fixed names


def test_the_backup_file_holds_the_whole_history_and_brings_it_back(owner, uploads):
    other = make_user(OTHER).id
    phone = _phone(owner)
    login = _login(phone, owner)
    devices.update_device(phone, DeviceFields(label="Renamed"), owner)
    _phone(other, "Theirs")
    history = [(r.created_at, r.change) for r in _revisions(owner)]
    with_login = _revisions(owner)[1].created_at

    written = backup.dump()
    content = Path(written.path).read_text(encoding="ascii")
    _lose_everything()
    loaded = backup.load(Path(written.path))
    again = backup.load(Path(written.path))

    assert (written.revisions, written.owners) == (4, 2)
    assert "danewalker" not in content
    assert "Phone 01" not in content
    assert (len(loaded.revisions), again.revisions) == (4, [])
    # Each person's own History, as it was: same moments, same words.
    assert [(r.created_at, r.change) for r in _revisions(owner)] == history
    assert [r.phones for r in _revisions(other)] == [1]
    assert _labels(owner) == []

    back = next(r for r in _revisions(owner) if r.created_at == with_login)
    store.restore(back.id, owner)

    assert _labels(owner) == ["Phone 01"]
    assert devices.secrets(login, owner).password == PASSWORD
    assert _labels(other) == []


def test_a_file_is_written_only_when_a_phone_changed(owner, uploads):
    phone = _phone(owner)

    first = backup.dump()
    unchanged = backup.dump()
    devices.update_device(phone, DeviceFields(label="Renamed"), owner)
    changed = backup.dump()

    assert (first.revisions, unchanged.path, changed.revisions) == (1, None, 2)
    assert changed.path == first.path
    assert len(backup.read(Path(changed.path)).states) == 2


def test_a_backup_is_unreadable_under_another_key(owner, uploads, monkeypatch):
    _phone(owner)
    written = backup.dump()

    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())

    with pytest.raises(VaultError):
        backup.read(Path(written.path))


def test_no_backup_is_written_without_the_vault_key(owner, uploads, monkeypatch):
    _phone(owner)
    monkeypatch.delenv("OFM_VAULT_KEY")

    with pytest.raises(VaultError):
        backup.dump()

    assert list(uploads.rglob("*.json*")) == []


def test_only_the_newest_backups_are_kept(owner, uploads, monkeypatch):
    _phone(owner)
    monkeypatch.setenv("OFM_PHONES_BACKUP_KEEP", "2")
    folder = uploads / "backups" / "phones"
    folder.mkdir(parents=True)
    for day in ("2020-01-01", "2020-01-02", "2020-01-03"):
        (folder / f"phones-{day}.json.enc").write_text("old", encoding="ascii")

    written = backup.dump()

    names = sorted(f.name for f in folder.glob("phones-*"))
    assert names == ["phones-2020-01-03.json.enc", Path(written.path).name]
