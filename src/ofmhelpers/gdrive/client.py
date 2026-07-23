"""
ofmhelpers/gdrive/client.py

Minimal Google Drive client: upload a local file into one specified folder.
Nothing else (no download, no listing) -- that's all this app needs.

Auth is OAuth as a real Google *user*, not a service account -- service
accounts have zero storage quota of their own, so uploading to a personal
(non-Workspace) Drive with one always fails with storageQuotaExceeded, no
matter how the destination folder is shared. Uploading as the actual user
spends their quota instead, which is what we want anyway (it's their Drive).

The one-time interactive consent (needs a browser) lives in
ofmhelpers/gdrive/authorize.py -- run that once locally, then copy the
resulting token file wherever GOOGLE_DRIVE_TOKEN_FILE points (see README).
At runtime this module only ever reads that token file and, when it's
expired, refreshes it silently -- no browser involved after the first time.
"""

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# drive.file: only sees/manages files this app itself created -- narrower
# than the full "drive" scope, so a leaked token can't read the rest of Drive.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Same convention as OFM_COOKIES_FILE/OFM_TODO_FILE elsewhere in this app --
# an env var override with a default that matches where the token actually
# lives (secrets/ is gitignored; see docker-compose.yml for how it's mounted
# into the container at the same relative path).
DEFAULT_TOKEN_FILE = "secrets/google-drive-token.json"


def _get_credentials() -> Credentials:
    token_file = Path(os.getenv("GOOGLE_DRIVE_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    if not token_file.is_file():
        raise FileNotFoundError(
            f"No Google Drive token at '{token_file}' -- run "
            "`uv run python -m ofmhelpers.gdrive.authorize` once locally (it "
            "opens a browser for you to grant access), then copy the token "
            "file it writes to that path. See README."
        )

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return creds


def _get_service():
    creds = _get_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_file(local_path: str | Path, folder_id: str | None = None) -> str:
    """Uploads local_path to the given (or GOOGLE_DRIVE_FOLDER_ID) Drive
    folder. Returns the new file's Drive id."""
    local_path = Path(local_path)
    folder_id = folder_id or os.environ["GOOGLE_DRIVE_FOLDER_ID"]

    service = _get_service()
    metadata = {"name": local_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), resumable=True)
    uploaded = (
        service.files().create(body=metadata, media_body=media, fields="id").execute()
    )
    return uploaded["id"]
