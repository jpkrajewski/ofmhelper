# Module purpose

Minimal Google Drive client: upload a local file into one specified folder.
Nothing else (no download, no listing). Auth is OAuth as a real Google
*user* (not a service account — service accounts have zero Drive storage
quota of their own, so uploads to a personal Drive always fail with them).

# Module files

- `authorize.py` — one-time interactive setup, run locally (`uv run python -m
  ofmhelpers.gdrive.authorize`): opens a browser for OAuth consent, saves a
  refresh token to `GOOGLE_DRIVE_TOKEN_FILE` (default
  `secrets/google-drive-token.json`). Needs a Desktop-app OAuth client JSON
  from Google Cloud Console (`GOOGLE_OAUTH_CLIENT_FILE`, only needed for this
  one-time run). Copy the resulting token file onto the server afterwards.
- `client.py` — `SCOPES` (`drive.file` only — narrower than full `drive`, so
  a leaked token can't read the rest of the user's Drive), `_get_credentials()`
  (reads the token file, refreshes silently if expired, no browser involved
  after the first run), and the actual upload function.

# Who calls this

The todo-approval flow (`web/routers/todo.py` / `web/approval_tokens.py`) —
approved assets get uploaded to Drive for VA/client handoff.
