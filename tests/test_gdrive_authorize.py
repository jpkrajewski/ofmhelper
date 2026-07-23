"""
Covers ofmhelpers/gdrive/authorize.py: the one-time interactive OAuth
consent script. InstalledAppFlow is mocked throughout -- this never opens a
real browser or talks to Google.
"""

import unittest.mock as mock

from ofmhelpers.gdrive import authorize


def _fake_flow(token_json='{"token": "fake"}'):
    fake_creds = mock.Mock()
    fake_creds.to_json.return_value = token_json
    fake_flow = mock.Mock()
    fake_flow.run_local_server.return_value = fake_creds
    return fake_flow, fake_creds


def test_main_uses_default_paths_and_writes_token_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_TOKEN_FILE", raising=False)
    fake_flow, fake_creds = _fake_flow('{"token": "default-path"}')

    with mock.patch.object(
        authorize.InstalledAppFlow,
        "from_client_secrets_file",
        return_value=fake_flow,
    ) as from_secrets:
        authorize.main()

    from_secrets.assert_called_once_with(
        authorize.DEFAULT_CLIENT_FILE, authorize.SCOPES
    )
    fake_flow.run_local_server.assert_called_once_with(port=0)

    token_file = tmp_path / "secrets" / "google-drive-token.json"
    assert token_file.is_file()
    assert token_file.read_text() == '{"token": "default-path"}'


def test_main_respects_env_var_overrides(tmp_path, monkeypatch):
    client_file = tmp_path / "my-client.json"
    client_file.write_text("{}")
    token_file = tmp_path / "out" / "token.json"
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(client_file))
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))
    fake_flow, fake_creds = _fake_flow('{"token": "overridden"}')

    with mock.patch.object(
        authorize.InstalledAppFlow,
        "from_client_secrets_file",
        return_value=fake_flow,
    ) as from_secrets:
        authorize.main()

    from_secrets.assert_called_once_with(str(client_file), authorize.SCOPES)
    assert token_file.read_text() == '{"token": "overridden"}'


def test_main_creates_missing_parent_directory_for_token_file(tmp_path, monkeypatch):
    # secrets/ (or whatever dir the token path is under) doesn't exist yet --
    # a fresh clone won't have it until something creates it.
    token_file = tmp_path / "does" / "not" / "exist" / "token.json"
    monkeypatch.setenv("GOOGLE_DRIVE_TOKEN_FILE", str(token_file))
    fake_flow, _ = _fake_flow()

    with mock.patch.object(
        authorize.InstalledAppFlow, "from_client_secrets_file", return_value=fake_flow
    ):
        authorize.main()

    assert token_file.is_file()
