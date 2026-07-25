"""
Covers ofmhelpers/gdrive/client.py: the OAuth-as-user Drive upload wrapper
(not a service account -- see client.py's docstring for why). Never talks to
the real Drive API -- google.oauth2.credentials and googleapiclient are
mocked throughout.
"""

import json
import unittest.mock as mock

import pytest

from ofmhelpers.gdrive import client


def _write_token(path, expired=False):
    path.write_text(
        json.dumps(
            {
                "token": "access-token",
                "refresh_token": "refresh-token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": client.SCOPES,
                "expiry": "2000-01-01T00:00:00Z" if expired else None,
            }
        )
    )


def test_upload_file_uses_saved_token_and_configured_folder(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    _write_token(token_file)
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-from-env")

    local_file = tmp_path / "asset.png"
    local_file.write_bytes(b"fake image bytes")

    fake_creds = mock.Mock(expired=False)
    fake_execute = mock.Mock(return_value={"id": "uploaded-file-id"})
    fake_create = mock.Mock(return_value=mock.Mock(execute=fake_execute))
    fake_service = mock.Mock()
    fake_service.files.return_value.create = fake_create

    with (
        mock.patch.object(
            client.Credentials, "from_authorized_user_file", return_value=fake_creds
        ) as from_file,
        mock.patch.object(client, "build", return_value=fake_service) as build,
    ):
        result = client.upload_file(local_file)

    from_file.assert_called_once_with(str(token_file), client.SCOPES)
    build.assert_called_once_with(
        "drive", "v3", credentials=fake_creds, cache_discovery=False
    )
    metadata = fake_create.call_args.kwargs["body"]
    assert metadata == {"name": "asset.png", "parents": ["folder-from-env"]}
    assert result == "uploaded-file-id"
    fake_creds.refresh.assert_not_called()


def test_expired_token_is_refreshed_and_rewritten_to_disk(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    _write_token(token_file, expired=True)
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))

    fake_creds = mock.Mock(expired=True, refresh_token="refresh-token")
    fake_creds.to_json.return_value = '{"refreshed": true}'

    with mock.patch.object(
        client.Credentials, "from_authorized_user_file", return_value=fake_creds
    ):
        creds = client._get_credentials()

    assert creds is fake_creds
    fake_creds.refresh.assert_called_once()
    assert token_file.read_text() == '{"refreshed": true}'


def test_expired_token_without_refresh_token_is_not_refreshed(tmp_path, monkeypatch):
    """Guards the `creds.expired and creds.refresh_token` check -- calling
    .refresh() with no refresh_token would just fail, so this must skip the
    refresh (and the write-back) entirely and hand back the stale creds
    as-is rather than crash."""
    token_file = tmp_path / "token.json"
    _write_token(token_file, expired=True)
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))

    fake_creds = mock.Mock(expired=True, refresh_token=None)

    with mock.patch.object(
        client.Credentials, "from_authorized_user_file", return_value=fake_creds
    ):
        creds = client._get_credentials()

    assert creds is fake_creds
    fake_creds.refresh.assert_not_called()


def test_upload_file_accepts_explicit_folder_id_over_env(tmp_path, monkeypatch):
    token_file = tmp_path / "token.json"
    _write_token(token_file)
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)

    local_file = tmp_path / "asset.mp4"
    local_file.write_bytes(b"fake video bytes")

    fake_creds = mock.Mock(expired=False)
    fake_execute = mock.Mock(return_value={"id": "another-id"})
    fake_create = mock.Mock(return_value=mock.Mock(execute=fake_execute))
    fake_service = mock.Mock()
    fake_service.files.return_value.create = fake_create

    with (
        mock.patch.object(
            client.Credentials, "from_authorized_user_file", return_value=fake_creds
        ),
        mock.patch.object(client, "build", return_value=fake_service),
    ):
        result = client.upload_file(local_file, folder_id="explicit-folder")

    assert fake_create.call_args.kwargs["body"]["parents"] == ["explicit-folder"]
    assert result == "another-id"


def test_missing_token_file_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(tmp_path / "does-not-exist.json"))
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-from-env")

    local_file = tmp_path / "asset.png"
    local_file.write_bytes(b"fake image bytes")

    with pytest.raises(FileNotFoundError, match="does-not-exist.json"):
        client.upload_file(local_file)


def test_uses_default_token_file_path_when_env_var_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_DRIVE_TOKEN_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="google-drive-token.json"):
        client._get_credentials()
