# Module purpose

Google Drive for /clone's "Upload to Drive": a user connects their Drive
with Google's normal consent page, and the worker uploads a batch into it.
Google's own libraries only (`google-auth-oauthlib`, `google-api-python-client`).

# Module files

- `oauth.py` — the OAuth 2.0 web-server flow (`Flow`, PKCE on by default):
  `authorization_url(next)` -> Google's page + the `PendingAuth` (state, code
  verifier, where to go back) the caller keeps in the session;
  `exchange_code` -> `Credentials` with a refresh token.
- `upload.py` — `account_email` (Drive `about.get`, so no extra identity
  scope), `upload_files(refresh_token, paths)` -> resumable uploads,
  `upload_workers` at once (one service object each: googleapiclient's HTTP
  is not thread-safe), into the app's folder (`settings.gdrive.folder_name`,
  created on first use). Every request retries itself (`execute(num_retries=
  upload_retries)`: Google's backoff on 5xx/429/drops, resuming an upload
  mid-file). Names already in the folder are skipped, so a retry never
  duplicates; one failed file never stops the rest. Returns an
  `UploadReport` (uploaded / already_there / failed + folder link).

# Who calls this

`web/routers/drive.py` (connect / callback / upload, the upload on the
worker).

# Setup (Google Cloud Console)

1. APIs & Services -> enable **Google Drive API**.
2. OAuth consent screen: External, add scope `.../auth/drive.file`,
   **Publish app** (In production). In "Testing", refresh tokens expire
   after 7 days.
3. Credentials -> OAuth client ID -> **Web application**, authorized
   redirect URI exactly `OFM_GDRIVE_REDIRECT_URI`
   (prod `https://ascendglobal.cash/drive/callback`).
4. `.env`: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
   `OFM_GDRIVE_REDIRECT_URI`. Unset id/secret = Upload answers 503.

# Findings

- `drive.file` is a **non-sensitive** scope (only files the app creates), so
  no Google security assessment is needed — the narrowest scope that can
  upload. It also means `files.list` sees only the app's own folder, never a
  same-named folder of the user's.
  https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- `Flow.authorization_url` sets `access_type=offline` itself; `prompt=consent`
  is what makes Google return a refresh token on a *repeat* connect too.
  https://googleapis.dev/python/google-auth-oauthlib/latest/_modules/google_auth_oauthlib/flow.html
- Testing-mode consent screens issue refresh tokens that die after 7 days;
  publishing to production removes that.
  https://www.unipile.com/google-oauth-refresh-token/
- The callback exchanges with `fetch_token(code=...)`, not the full
  `authorization_response` URL: behind nginx the app sees `http://`, and
  oauthlib refuses a non-https response URL.
