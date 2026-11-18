# pinterest

pinterest.com's own web calls (what its "Create Pin" page sends), made with an account's logged-in cookies: no developer app, no review. Used by `features/distribution` (/distribution).

## Why not the official API (researched 2026-11-18)
- v5 needs a developer app tied to a Business account. Trial access is itself backlogged (a Pinterest "KNOWN ISSUE" post, Aug-Oct 2026, one report pending 26 days). Trial pins are sandbox-only, visible to their creator, and the sandbox has no video pins. Standard access needs a demo video of the OAuth flow; reported waits are 2-5+ weeks.
- The developer guidelines say "the end user must choose each Pin to be published", so an auto-poster fails review. Sources: developers.pinterest.com/docs/key-concepts/access-tiers/, policy.pinterest.com/en/developer-guidelines, community.pinterest.biz/t/known-issue-support-for-trial-access-pending-or-denied-access/50249.
- Paid aggregators were checked too (owner: "free api pls"). Upload-Post fit best ($50/mo for 25 profiles). Ayrshare's terms ban "sexually suggestive" content.
- Owner's call: free and automatic from day one, so cookies. The risk is Pinterest's rules (suggestive content, redirects to sites it refuses, multi-account automation): accounts can be banned whatever posts for us.

## The calls (recorded in a logged-in Chrome 2026-11-18, two real pins)
- Every call goes to `/resource/ApiResource/{get|create}/` with `source_url` + `data={"options": {"url": "/v3/...", "data": {...}}, "context": {}}`. A get sends these as the query; a create sends them as a form with `X-CSRFToken` = the `csrftoken` cookie.
- **`X-Pinterest-PWS-Handler` is required.** Without it the answer is 403 "Invalid Resource Request" (checked). `x-app-version` is not needed, so there is no 1.2 MB home-page fetch.
- **Image pin:**
  1. `/v3/media/uploads/register/batch/` with `media_info_list` (JSON in a string), `media_type: image-story-pin`, returns a signed S3 slot.
  2. POST the file to `upload_url` as multipart: exactly `upload_parameters`, then `file` → 204.
  3. Poll `/v3/media/uploads/` with `upload_ids` until `status: succeeded` with `signature` (0.4 s for a picture; ~1.5-4 min end to end for a 36 MB reel).
  4. `/v3/storypins/` with `story_pin` (JSON in a string: one page, one block `type 2` + `image_signature`, `metadata.pin_title`, `canvas_aspect_ratio` = w/h), `board_id`, `description`, `link`. The answer has `data.id` (the pin).
- **Video pin:** same steps with `video-story-pin` + `upload_aux_data.clips[].durationMs`. The block is `type 3` + `video_signature`. A cover is required: the first frame (ffmpeg) uploaded as an image, its signature as `metadata.pin_image_signature`.
- **Board create:** `/resource/BoardResource/create/` with options `{name, description, privacy: public, layout: default, category: other}`, `source_url: /`, handler `www/[username].js`. A new account has no board at all.
- **Account / boards:** `/v3/users/me/` (`user.username`), `/v3/users/me/boards/`.

## Traps (each one hit live)
- **S3 403 `Policy Condition failed: ["eq", "$Content-Type", "multipart/form-data"]`.** The signed fields hold `Content-Type: multipart/form-data`. Sending the file's own type in its place breaks the policy: send the fields untouched.
- **`CookieConflictError: multiple cookies with name 'csrftoken'`.** A whole-browser export has one per host (`www.` and the locale host `pl.pinterest.com`: Pinterest sends a Polish account there).
  - The client picks the locale host when the export has one, else www.
  - It keeps only the cookies a browser sends to that host, and that host's csrftoken.
  - `only_pinterest` drops every other site's cookies before anything is stored (an export was over 1 MB).
- **`SSLError` once in 14 uploads to S3.** The upload is tried `OFM_PINTEREST_UPLOAD_TRIES` (3) times; the same signed slot again is safe.
- **No idempotency key:** a pin created just before a killed worker is made again on retry (the sweep fails a post left `publishing` past the job time).

## Errors
- `PinterestRateLimitedError` (429, or `http_status` 429 in the body): the post goes back in line untried, and the account waits `Retry-After` or `OFM_DISTRIBUTION_RATE_LIMIT_PAUSE_S`.
- `PinterestLoggedOutError` (any 401/403): the account stops until new cookies. "Invalid Resource Request" is logged as an error, because it can also mean a missing header.
- `PinterestError`: anything else. Every refusal or non-JSON answer is logged with its status and a 240-character snippet, never a cookie.

## Files
- `client.py` `PinterestClient(cookies)`: `me`, `boards`, `board(name)` (found or made), `upload`, `processed`, `create_pin`.
- `models.py`: answers and the `PinCreate` body.
- `fake.py`: the wire fake (www.pinterest.com + the S3 host). Username `pin_<_pinterest_sess>`, uploads processing once then succeeded, `fail_next`, `logged_out`.
- Tests:
  - `tests/integration/web/test_distribution.py`
  - `tests/unit/pinterest/test_pinterest_cookies.py`
- Settings `OFM_PINTEREST_*`.
