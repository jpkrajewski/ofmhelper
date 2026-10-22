# signin

How people sign in (`settings.signin`, `OFM_*` in config/settings.py `SignInSettings`). Used by `web/routers/auth.py`, `web/routers/account.py`, `web/stores/users.py`.

- `base.py` — `Provider` protocol (`consent_url`, `exchange`, `identity`, `callback_method`), `Identity`, `SignInError` (message safe to show), `claims` (id-token payload), `checked` (nonce), `token_request` (via `utils.outbound.post`).
- `google.py` — sign-in asks only `openid email profile` (non-sensitive: no "unverified app" screen); connecting on /account (`connect=True`, `/auth/google/start?link=1`) adds the restricted `drive` scope with `access_type=offline` + `prompt=consent` (Google hands out a refresh token only there; that one step shows the unverified warning until a paid CASA assessment); `drive_credentials(refresh_token)` for `gdrive.connect_as`. A new Drive token queues that person's Drive sync (routers/auth.py `_sync_new_drive`).
- `apple.py` — Sign in with Apple; `response_mode=form_post` (Apple POSTs the callback); client secret = ES256 JWT signed with the .p8 per exchange (`cryptography`, no JWT lib).
- `passwords.py` — stdlib `hashlib.scrypt` (n=2^17, r=8, p=1; ~128 MiB, OWASP; params stored in the hash), at most `OFM_PASSWORD_HASH_CONCURRENCY` (4) at once per process, constant-time verify, set-password link tokens (only SHA-256 stored).
- `fake.py` — token endpoints of both, in process (OFM_FAKE_EXTERNALS); consent screens are `/auth/fake-consent`.
- New provider = one module + one entry in `providers()`.

## Rules (stores/users.py)
- Nobody signs up: an admin adds the email on /users (gets a one-time set-password link, 7 days, a new one voids the old). `OFM_ADMIN_EMAILS` always get in as admins (made on the spot).
- Google/Apple: linked by the provider's `sub` (`user_identities`); first time by verified email. Apple may give a private-relay email: once linked it no longer matters.
- Email "admin" + `APP_PASSWORD_ADMIN` = the password admin account (break-glass; owns all pre-users data).
- Session = signed cookie with user id/email/role + `credential_stamp` (digest of the password hash), re-checked every request (deactivated/removed user is out at the next click; role change applies at once; a password change signs out every other session).
- `OFM_FAKE_EXTERNALS=1` with `OFM_ENV=production` fails settings validation (fake consent signs in as any email).
- State + nonce + next + link travel in a signed cookie `ofm_signin` (path /auth/, SameSite=None+Secure in prod: Apple's cross-site POST would drop a Lax cookie).

## Setup (one time, by hand — consoles, no API)
Google (Cloud project of the Drive bot, `deft-scope-498514-b7`):
1. APIs & Services -> OAuth consent screen: External, app name, support email; scopes `openid`, `email`, `profile`, `.../auth/drive`; Branding: home `https://ascendglobal.cash`, privacy `/privacy`, terms `/terms`, authorized domain `ascendglobal.cash`; Audience -> **Publish app** ("In production"). Unverified is fine for us: sign-in shows no warning; connecting Drive shows "Google hasn't verified this app" once (Advanced -> continue); cap 100 users. Testing mode would expire refresh tokens after 7 days (Drive disconnects weekly).
2. Credentials -> Create OAuth client ID -> Web application. Authorized redirect URIs: `https://ascendglobal.cash/auth/google/callback` (+ `http://localhost:8000/auth/google/callback` for dev).
3. `.env`: `OFM_GOOGLE_CLIENT_ID=...`, `OFM_GOOGLE_CLIENT_SECRET=...`, `OFM_ADMIN_EMAILS=you@gmail.com`, `APP_BASE_URL=https://ascendglobal.cash/` (prod).

Apple (needs a paid Apple Developer account):
1. Certificates, Identifiers & Profiles -> Identifiers -> App ID with "Sign in with Apple"; then a Services ID (e.g. `cash.ascendglobal.web`) -> Sign in with Apple -> Configure: domain `ascendglobal.cash`, return URL `https://ascendglobal.cash/auth/apple/callback` (Apple requires https: no localhost).
2. Keys -> new key with Sign in with Apple -> download the .p8 (once), note Key ID and Team ID.
3. `.env`: `OFM_APPLE_SERVICES_ID`, `OFM_APPLE_TEAM_ID`, `OFM_APPLE_KEY_ID`, `OFM_APPLE_PRIVATE_KEY='<the .p8 file on one line, line breaks written as
>'`.

A provider shows on /login only when its settings are complete.

## Findings
- Id tokens straight from the token endpoint over TLS need no signature check (OpenID Connect Core 3.1.3.7); the nonce still must match. https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation
- Apple sends `email_verified` as the string "true"; the name only on the very first consent (form field `user`). https://developer.apple.com/documentation/sign_in_with_apple/request_an_authorization_to_the_sign_in_with_apple_server
- Google returns a refresh token only with `access_type=offline` on a consent screen (`prompt=consent`). https://developers.google.com/identity/protocols/oauth2/web-server#offline
- `drive` is a restricted scope: unverified app -> warning screen + 100-user cap, no 7-day expiry once "In production". https://support.google.com/cloud/answer/13463073
