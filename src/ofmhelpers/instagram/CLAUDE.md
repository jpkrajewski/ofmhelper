# instagram

Profiles' reels (play counts, length, mp4) via logged-out web GraphQL.

- `client.py` — `InstagramClient`, the only code calling Instagram: `open_profile`, `reels`, `timeline`, `media` (falls back to logged-in `media/{pk}/info/` with cookies.txt for gated reels), `download_video`. `doc_id`s are settings; they rotate every few weeks ("Instagram changed its ... query").
- `models.py` — response shapes, `ProfileHandle`, `ReelSummary.posted_at` (from the id), `Reel.tier` (`VIEW_TIERS`), `ReelDownload`.
- `scan.py` — `scan_profile`: age/min_views from the reels tab, mp4+length from the timeline, `media` only for gaps, then the length cap.

Callers: `web/routers/downloads/reels.py`, `web/reel_watch.py`.

## How Instagram answers (verified 2026-10-17)
- `GET /<user>/reels/` (navigation headers) -> `profile_id` + `lsd` token in the HTML.
- `POST /api/graphql` (`lsd`, `doc_id`, `variables`; headers `X-FB-LSD`, `X-IG-App-ID: 936619743392459`, `X-ASBD-ID`, `X-FB-Friendly-Name`):
  - `PolarisProfilePageContentQuery` -> `follower_count`, `is_private`.
  - `PolarisProfileReelsTabContentQuery_connection` -> `pk`, `code`, `play_count`, thumbnail, pinned flag; no length/mp4/`taken_at`. `pk >> 23` = ms since 1314220021721.
  - `PolarisLoggedOutDesktopWWWPostRootContentQuery` -> mp4, DASH length, `taken_at` (403 on `/graphql/query`).
  - `PolarisProfilePostsTabContentQuery_connection` -> timeline with mp4/length/`taken_at`, 12/page, no views; hidden accounts answer error 1357005.
- Gated accounts: logged-out post query returns null; `GET /api/v1/media/{pk}/info/` with cookies works. Without a login the reel is still listed as "login only".
- Doesn't work: anonymous REST (`web_profile_info`, `clips/user`) 429/401; gallery-dl/yt-dlp lack play counts (still the best `doc_id` reference); embed HTML has only rounded followers.
- Media URLs are moved to `scontent.cdninstagram.com` (`MediaUrl`): regional hosts like `instagram.ftpa1-2.fna.fbcdn.net` can be IPv6-only, unreachable for IPv4-only viewers and Docker; the global host serves the same signed paths (verified 2026-10-19).
- CDN allows embedding (CORP cross-origin, CORS *), so /reels uses URLs directly. URLs expire (~1.5 days). Rate-limited per IP: scan one profile at a time.
