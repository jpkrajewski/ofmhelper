# socials

Tracked social accounts, platform-agnostic. Each platform turns a handle into `AccountStats`; `web/account_tracker.py` scans on a schedule, `/accounts` (`web/routers/tracking/accounts.py`) shows them.

- `models.py` — `AccountProfile`, `PostStat`, `AccountStats` (`complete` = no cap hit; `.metrics(periods_days)` keeps a window per period and its double, for vs-previous), `AccountMetrics` (+ `ViewWindow.covered`: the scan reached the window start, else its numbers read as unknown, never as a floor). `PostStat.thumbnail_url`.
- `errors.py` — the only exceptions `fetch` raises: `SocialError` (lasting, recorded), `RetryableError` (`reached`, `dead_proxy`), `RateLimitedError` (stops the batch when every try hit it).
- `platforms/base.py` — `Platform` ABC: `key`, `label`, `handle()`, `profile_url()`, `proxies()`, `fetch(handle, proxy)`.
- `platforms/instagram.py` — `ofmhelpers.instagram` client: profile (logged out, via proxies) + newest `OFM_IG_TRACK_MAX_REELS` (96) reels' play counts. Reels come from the uploaded login (`InstagramClient.reels_logged_in`: the reels-tab GraphQL query sent as the login, no proxy) when it works (`OFM_IG_TRACK_LOGGED_IN`), else the logged-out reels tab, and the scan is marked `limited` (UI banner asks for fresh cookies). Private: profile numbers, no posts.

## Instagram hides reels from logged-out visitors (verified 2026-10-20)
- @danywalking: 15 reels in 7 days on Instagram, the logged-out reels tab and timeline both end after 8 (`has_next_page` false), any page size, direct or proxied. @instagram logged out: 96+. So it is per reel (sensitive/age-restricted content), not a page cap; `total_clips_count`/`media_count` give no signal logged out.
- Only a login sees them: the reels-tab GraphQL query (`/graphql/query`, `av` = page `actorID`, `fb_dtsg` from `DTSGInitialData`, same `doc_id`) returned all 15. The REST API (`/api/v1/clips/user/`, `feed/user`, `current_user`) 302s cookie sessions to the home page even when the website is logged in -- don't use it.
- A dead login: its page has `"actorID":null` -> `reels_logged_in` returns None -> logged-out fallback, scan `limited`, banner on /accounts. Uploading cookies at `/cookies` resyncs every limited account at once (`account_tracker.rescan_limited`).
- `platforms/__init__.py` — `platforms()` registry (lru_cache), `get_platform(key)`.

**New platform (TikTok, Threads, X, Snapchat...)** = `platforms/<name>.py` subclass + one entry in `_PLATFORM_TYPES` (+ its settings group for proxies/limits). Page, filters, add dialog, tracker, DB need no change. Never rename a `key` in use (stored on every account).

Storage (`web/db/models.py`): `tracked_accounts` (latest `profile`/`metrics`/`posts` JSONB, `creator_id`, `notes`), `account_snapshots` (`followers` column + `metrics` JSONB per successful scan; growth = newest snapshot a full period old; younger accounts show change since the first snapshot, marked partial; one snapshot = no growth), `creators`.
Settings: `AccountsSettings` (`OFM_ACCOUNTS_*`): rescan 6 h, batch 20/job, periods 7/30 d (`OFM_ACCOUNTS_PERIODS_DAYS`, first = default), top posts 10, page sizes.
