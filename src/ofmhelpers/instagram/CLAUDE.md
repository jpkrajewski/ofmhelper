# Module purpose

Instagram profiles' reels with play counts, length and mp4, through the
logged-out web GraphQL (no cookies needed), and which of them went viral.

# Module files

- `client.py` — `InstagramClient`, the only code calling Instagram:
  `open_profile` (profile page -> `lsd` token + user id, then follower count),
  `reels` (reels tab, paged), `media` (one reel's mp4 + DASH length; for a
  reel gated from logged-out visitors, falls back to the logged-in
  `media/{pk}/info/` with cookies.txt),
  `download_video` (CDN, only on "Clone selected"). Query `doc_id`s are
  settings (`settings.instagram.*_doc_id`); Instagram rotates them every few
  weeks — a stale one surfaces as "Instagram changed its ... query".
- `models.py` — Pydantic shapes of the three answers, plus `ProfileHandle`
  (username/URL parsing), `ReelSummary.posted_at` (post time decoded from the
  media id), `Reel` with its `tier` (`VIEW_TIERS`, the /reels border colours),
  `ProfileReels`, and the CDN-only `ReelDownload` (URLs the browser posts
  back).
- `scan.py` — `scan_profile`: age (`max_age_s`, from the id — free) and
  `min_views` from the reels tab only; mp4 + length of the survivors from
  the posts timeline (`client.timeline`, 12 posts a request, read until it
  has them all or is past the window); per-reel `media` only for what the
  timeline lacks. Then the length cap. Most viewed first. Measured
  2026-10-17: sheep_love33 15 reels in 18 requests (was ~37), kikiumi2026
  (hidden account, timeline errors) 3 in 10.

# Who calls this

`web/routers/downloads/reels.py` (`/reels`) and `web/reel_watch.py` (the 12 h
rescan of watched profiles).

# How Instagram answers (verified 2026-10-17)

What works, logged out, from a plain `requests.Session` (no cookies, no TLS
impersonation):

1. `GET https://www.instagram.com/<user>/reels/` with navigation headers
   (`Sec-Fetch-Mode: navigate`, ...). The HTML carries `"profile_id":"<id>"`
   and the `lsd` token (`"LSD",[],{"token":"..."}`). Sets a `csrftoken`
   cookie on the session; nothing else is needed.
2. `POST https://www.instagram.com/api/graphql`, form body `lsd`, `doc_id`,
   `variables` (JSON), `fb_api_req_friendly_name`, `av=0`, `__a=1`, ...;
   headers `X-FB-LSD`, `X-IG-App-ID: 936619743392459`, `X-ASBD-ID`,
   `X-FB-Friendly-Name`. Three queries:
   - `PolarisProfilePageContentQuery` (`variables.id` = profile id) ->
     `data.user.follower_count` (exact), `is_private`.
   - `PolarisProfileReelsTabContentQuery_connection` ->
     `data.fetch__XDTUserDict.clips_connection.edges[].node.media`: `pk`,
     `code`, `play_count` (the number the app shows), thumbnail in
     `image_versions2`, `clips_tab_pinned_user_ids` (non-empty = pinned,
     listed first whatever its age); **no** length, **no** mp4, no
     `taken_at` — but `pk >> 23` is ms since 1314220021721 (Instagram's id
     epoch), within seconds of `taken_at`. Newest first after the pinned
     ones. Paged by `page_info.end_cursor` -> `variables.after`.
   - `PolarisLoggedOutDesktopWWWPostRootContentQuery` (`variables.media_id`
     = `pk`) -> `data.xig_polaris_media.if_not_gated_logged_out`:
     `video_versions[0].url` (mp4), `video_dash_manifest` (length from
     `mediaPresentationDuration="PT14.8S"`; `video_duration` is null here),
     `taken_at`. This one 403s on `/graphql/query` — use `/api/graphql` for
     all three.

   - `PolarisProfilePostsTabContentQuery_connection` (`variables.username`)
     -> `data.xdt_api__v1__feed__user_timeline_graphql_connection.edges[]
     .node`: the posts grid (photos too: `video_versions` null), each video
     with `video_versions` + `video_dash_manifest` + `taken_at`, 12 a page;
     **no** play counts (`view_count` null). For an account hidden from
     visitors it answers `errors` (code 1357005) and no `data`.
3. **Gated reels** (verified 2026-10-17 on `kikiumi2026`, `bunny36brownie`):
   Instagram hides some accounts' reels from logged-out visitors. The reels
   tab still lists them with `play_count`, but the logged-out reel query
   answers `{"data": {"xig_polaris_media": null}}` and `/reel/<code>/embed/`
   has no video. For every reel of such an account, not just some.
   `GET /api/v1/media/{pk}/info/` **with the cookies.txt session** answers
   them: `items[0].video_versions[0].url`, `items[0].video_duration`
   (seconds), `taken_at`. A dead session gets a 302 to `/`. With no working
   login the scan still lists the reel (views, thumbnail, date from the id)
   as "🔒 login only", so a viral reel is never silently dropped.

What does not work (why this module looks the way it does):

- REST `GET /api/v1/users/web_profile_info/` and `POST /api/v1/clips/user/`:
  429 or `401 {"require_login": true}` anonymously. With the cookies.txt
  session, `web_profile_info` was 429 and `clips/user` / `current_user` 302'd
  to `/` — but `media/{pk}/info/` with the same session works (above). So a
  302 from one logged-in endpoint does not mean the session is dead.
- gallery-dl / yt-dlp: neither exposes play counts for a profile's reels
  (gallery-dl's reels extractor also spends one request per reel at 6–12 s
  spacing). Both are still the best reference for current `doc_id`s:
  gallery-dl's `QUERIES` dict in `gallery_dl/extractor/instagram.py` (it also
  scrapes fresh ids from instagram.com's JS bundles), yt-dlp's
  `extractor/instagram.py` for the logged-out post query.
- Logged-out profile/embed HTML (`/<user>/embed/`, crawler user agents): only
  a rounded follower count in `og:description`, no reel list.
- The CDN (`*.cdninstagram.com`, `*.fbcdn.net`) **does** allow embedding:
  `cross-origin-resource-policy: cross-origin`, `Access-Control-Allow-Origin:
  *`, `Accept-Ranges: bytes` on both mp4 and jpg. A first check that seemed
  to say otherwise (403) was a truncated URL. So /reels uses the URLs
  directly in `<video src poster>` — no proxy.
- CDN URLs are signed and expire (`oe=` hex epoch, ~1.5 days out) — fine for
  a 12 h rescan cycle, not for long-term storage.
- Logged-out calls are rate-limited per IP; scan profiles one at a time.

Sources:
- gallery-dl instagram extractor (installed copy) — query names, variables,
  headers, doc_id discovery.
- yt-dlp instagram extractor (installed copy) — logged-out post query.
- https://seotanvirbd.com/how-to-scrape-instagram-reels-using-python-graphql-api-no-authentication-required/
  — POST `/graphql/query` + `doc_id` shape for a single reel.
- https://scrapfly.io/blog/posts/best-open-source-instagram-scrapers —
  doc_ids rotate every 2–4 weeks; anonymous REST is 429'd within minutes.
