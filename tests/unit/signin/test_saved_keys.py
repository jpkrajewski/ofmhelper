# Copyright (C) 2026 jpkrajewski

import pytest
from pydantic import BaseModel, ValidationError

from ofmhelpers.db.users.schema import (
    ApiKeysForm,
    Identity,
    PasswordForm,
    SavedKey,
    SavedKeys,
)


def test_a_saved_key_is_found_by_its_provider():
    saved = SavedKeys(
        keys=[
            SavedKey(provider="kie", api_key="kie-key-1111"),
            SavedKey(provider="oopsie", api_key="oopsie-key-2222"),
        ],
    )

    assert saved.get("oopsie") == "oopsie-key-2222"


def test_a_provider_without_a_saved_key_gives_an_empty_string():
    assert SavedKeys().get("kie") == ""


def assert_masked(model: BaseModel, secret: str) -> None:
    assert secret not in repr(model)
    assert secret not in model.model_dump_json()


def test_a_saved_key_masks_its_api_key_but_returns_its_secret_value():
    key = SavedKey(provider="kie", api_key="saved-key-secret")

    assert_masked(key, "saved-key-secret")
    assert key.api_key.get_secret_value() == "saved-key-secret"


def test_a_password_form_masks_passwords_but_returns_their_secret_values():
    form = PasswordForm(password="password-secret", confirm="password-secret")

    assert_masked(form, "password-secret")
    assert form.password.get_secret_value() == "password-secret"
    assert form.confirm.get_secret_value() == "password-secret"


def test_an_api_keys_form_masks_keys_but_returns_their_secret_values():
    form = ApiKeysForm(keys={"kie": "api-key-secret"})

    assert_masked(form, "api-key-secret")
    assert form.keys["kie"].get_secret_value() == "api-key-secret"


def test_an_identity_masks_its_refresh_token_but_returns_its_secret_value():
    identity = Identity(
        provider="google",
        subject="subject-1",
        refresh_token="refresh-token-secret",
    )

    assert_masked(identity, "refresh-token-secret")
    assert identity.refresh_token is not None
    assert identity.refresh_token.get_secret_value() == "refresh-token-secret"


def test_an_api_keys_form_trims_keys_and_drops_empty_values():
    form = ApiKeysForm(keys={"kie": "  kie-key-1111  ", "empty": " \t\n "})

    assert set(form.keys) == {"kie"}
    assert form.keys["kie"].get_secret_value() == "kie-key-1111"


def test_a_password_form_requires_both_passwords():
    with pytest.raises(ValidationError):
        PasswordForm(password="", confirm="valid-password")
    with pytest.raises(ValidationError):
        PasswordForm(password="valid-password", confirm="")


def test_a_password_form_enforces_the_256_character_limit():
    password = "p" * 256
    form = PasswordForm(password=password, confirm=password)
    assert form.password.get_secret_value() == password
    assert form.confirm.get_secret_value() == password

    too_long = "p" * 257
    with pytest.raises(ValidationError):
        PasswordForm(password=too_long, confirm="valid-password")
    with pytest.raises(ValidationError):
        PasswordForm(password="valid-password", confirm=too_long)


def test_an_api_keys_form_enforces_the_512_character_limit():
    api_key = "k" * 512
    form = ApiKeysForm(keys={"kie": api_key})
    assert form.keys["kie"].get_secret_value() == api_key

    with pytest.raises(ValidationError):
        ApiKeysForm(keys={"kie": "k" * 513})


@pytest.mark.parametrize("keys", [None, {"kie": 123}])
def test_an_api_keys_form_rejects_malformed_keys(keys):
    with pytest.raises(ValidationError):
        ApiKeysForm(keys=keys)
