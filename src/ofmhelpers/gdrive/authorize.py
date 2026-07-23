"""
ofmhelpers/gdrive/authorize.py

One-time interactive setup for gdrive/client.py: opens a browser for you to
grant this app access to your Google Drive, then saves a refresh token to
GOOGLE_DRIVE_TOKEN_FILE (default secrets/google-drive-token.json).

Run this locally -- it needs a browser, so it won't work inside the server
container. Afterwards, copy the token file it writes onto the server at
whatever path GOOGLE_DRIVE_TOKEN_FILE points to there:

    uv run python -m ofmhelpers.gdrive.authorize

Needs an OAuth client (Desktop app type) downloaded from Google Cloud
Console as JSON -- see README for how to create one. Defaults to
secrets/google-oauth-client.json; override with GOOGLE_OAUTH_CLIENT_FILE.
That client file itself is only needed for this one-time run, never on the
server -- the saved token embeds what's needed to refresh itself.
"""

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from ofmhelpers.gdrive.client import SCOPES, DEFAULT_TOKEN_FILE

DEFAULT_CLIENT_FILE = "secrets/google-oauth-client.json"


def main() -> None:
    client_file = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", DEFAULT_CLIENT_FILE)
    token_file = Path(os.getenv("GOOGLE_DRIVE_TOKEN_FILE", DEFAULT_TOKEN_FILE))

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    print(f"Saved Google Drive token to {token_file}")


if __name__ == "__main__":
    main()
