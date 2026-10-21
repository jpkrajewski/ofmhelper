# instagram

Profiles' reels (play counts, length, mp4) via logged-out web GraphQL.

- `client.py` — `InstagramClient`, the only code calling Instagram: `open_profile`, `reels`, `reels_logged_in` (same GraphQL query as the uploaded login; `_query(..., login=LoginTokens)`), `timeline`, `media` (falls back to logged-in `media/{pk}/info/` with cookies.txt for gated reels), `download_video`. `doc_id`s are settings; they rotate every few weeks ("Instagram changed its ... query").
- `models.py` — response shapes, `ProfileHandle`, `ReelSummary.posted_at` (from the id), `Reel.tier` (`VIEW_TIERS`), `ReelDownload`.
- `pages.py` — HTML parsing only: `reels_page` (profile id + `lsd`), `login_tokens`, `is_profile_page`, `crawler_sees_a_profile`. The client fetches, `pages`/`models` parse (offline tests on `tests/fixtures/scraper/`).
- `fake.py` + `fake_profiles.json` — Instagram + CDN at the wire for OFM_FAKE_EXTERNALS/tests (via `utils/outbound.py`): the real client parses it. `FakeInstagram.proxy_modes` = dead/timeout/rate_limited/banned per proxy; `calls` log. Kept honest by `tests/contract/test_instagram_contract.py`.
- `scan.py` — `scan_profile`: age/min_views from the reels tab, mp4+length from the timeline, `media` only for gaps, then the length cap.

Callers: `web/routers/downloads/reels.py`, `web/reel_watch.py`, `socials/platforms/instagram.py` (/accounts tracker).

## How Instagram answers (verified 2026-10-17)
- `GET /<user>/reels/` (navigation headers) -> `profile_id` + `lsd` token in the HTML.
- `POST /api/graphql` (`lsd`, `doc_id`, `variables`; headers `X-FB-LSD`, `X-IG-App-ID: 936619743392459`, `X-ASBD-ID`, `X-FB-Friendly-Name`):
  - `PolarisProfilePageContentQuery` -> `follower_count`, `following_count`, `is_private`, `is_verified`, `full_name`, `profile_pic_url` + `hd_profile_pic_url_info.url` (verified 2026-10-20; `media_count` is null logged out).
  - `PolarisProfileReelsTabContentQuery_connection` -> `pk`, `code`, `play_count`, thumbnail, pinned flag; no length/mp4/`taken_at`. `pk >> 23` = ms since 1314220021721.
  - `PolarisLoggedOutDesktopWWWPostRootContentQuery` -> mp4, DASH length, `taken_at` (403 on `/graphql/query`).
  - `PolarisProfilePostsTabContentQuery_connection` -> timeline with mp4/length/`taken_at`, 12/page, no views; hidden accounts answer error 1357005.
- Logged-out visitors don't see sensitive/age-restricted reels at all: the reels tab just ends early (danywalking 8 of 15, 2026-10-20). Logged-in GraphQL (`/graphql/query` + `av`/`fb_dtsg` from the logged-in page) lists them; logged-in REST 302s to home.
- Gated accounts: logged-out post query returns null; `GET /api/v1/media/{pk}/info/` with cookies works. Without a login the reel is still listed as "login only".
- Doesn't work: anonymous REST (`web_profile_info`, `clips/user`) 429/401; gallery-dl/yt-dlp lack play counts (still the best `doc_id` reference); embed HTML has only rounded followers.
- Media URLs are moved to `scontent.cdninstagram.com` (`MediaUrl`): regional hosts like `instagram.ftpa1-2.fna.fbcdn.net` can be IPv6-only, unreachable for IPv4-only viewers and Docker; the global host serves the same signed paths (verified 2026-10-19).
- CDN allows embedding (CORP cross-origin, CORS *), so /reels uses URLs directly. URLs expire (~1.5 days). Rate-limited per IP: scan one profile at a time.
