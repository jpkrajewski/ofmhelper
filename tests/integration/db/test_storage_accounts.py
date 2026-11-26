# Copyright (C) 2026 jpkrajewski

import pytest
from cryptography.fernet import Fernet
from signin import make_user
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ofmhelpers.db.session import session_scope
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import StorageProvider


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())


@pytest.fixture
def user_id() -> int:
    return make_user("storage@example.com").id


def test_a_saved_account_reads_back_without_its_token(user_id):
    storage_accounts.save(user_id, StorageProvider.DROPBOX, "tok-1", "dbid:1", "a@b.co")

    found = storage_accounts.get(user_id, StorageProvider.DROPBOX)

    assert found is not None
    assert found.account_id == "dbid:1"
    assert found.email == "a@b.co"
    assert "tok-1" not in found.model_dump_json()
    assert storage_accounts.refresh_token(user_id, StorageProvider.DROPBOX) == "tok-1"


def test_saving_again_replaces_the_token_account_and_email(user_id):
    storage_accounts.save(user_id, StorageProvider.DROPBOX, "tok-1", "dbid:1", "a@b.co")

    storage_accounts.save(user_id, StorageProvider.DROPBOX, "tok-2", "dbid:2", "c@d.co")

    found = storage_accounts.get(user_id, StorageProvider.DROPBOX)
    assert found is not None
    assert (found.account_id, found.email) == ("dbid:2", "c@d.co")
    assert storage_accounts.refresh_token(user_id, StorageProvider.DROPBOX) == "tok-2"


def test_an_unconnected_person_has_no_account_and_no_token(user_id):
    assert storage_accounts.get(user_id, StorageProvider.DROPBOX) is None
    assert storage_accounts.refresh_token(user_id, StorageProvider.DROPBOX) is None


def test_only_dropbox_can_be_stored(user_id):
    with pytest.raises(IntegrityError):
        storage_accounts.save(user_id, StorageProvider.GDRIVE, "t", "id", "e")


def test_the_token_is_encrypted_at_rest_and_masked_in_the_audit_log(user_id):
    storage_accounts.save(user_id, StorageProvider.DROPBOX, "tok-secret", "id", "e")

    with session_scope() as s:
        stored = s.execute(
            text("SELECT refresh_token FROM storage_accounts WHERE user_id = :u"),
            {"u": user_id},
        ).scalar_one()
        logged = (
            s.execute(
                text(
                    "SELECT changes::text FROM audit_log"
                    " WHERE table_name = 'storage_accounts' AND row_id = :r",
                ),
                {"r": f"{user_id}:dropbox"},
            )
            .scalars()
            .all()
        )

    assert "tok-secret" not in stored
    assert logged
    assert all("tok-secret" not in entry for entry in logged)


def test_the_storage_provider_defaults_to_google_and_switches_both_ways(user_id):
    assert users.get_user(user_id).storage_provider == StorageProvider.GDRIVE

    users.set_storage_provider(user_id, StorageProvider.DROPBOX)
    assert users.get_user(user_id).storage_provider == StorageProvider.DROPBOX

    users.set_storage_provider(user_id, StorageProvider.GDRIVE)
    assert users.get_user(user_id).storage_provider == StorageProvider.GDRIVE
