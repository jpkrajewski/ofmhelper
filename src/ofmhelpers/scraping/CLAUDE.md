# Module purpose

Scraping and ranking social posts (Instagram/TikTok reels) via Apify actors,
then exporting the results to a formatted spreadsheet for review.

# Module files

- `apify.py` — `get_client_with_most_credits(api_keys)` picks whichever
  configured Apify API key has the most remaining monthly credit;
  `run_actor(client, actor_id, raw_input)` runs an actor and returns its
  dataset items as a list.
- `models.py` — `PostBase` (base dataclass: username, url, timestamp,
  views/likes/comments, caption, duration, hashtags, `is_valid()`), `Reel`
  and `TikTokVideo` (platform-specific subclasses with `from_apify(item)`
  classmethods to normalize a raw Apify dataset item), `TikTokAuthor`
  (nested author info for TikTok items).
- `post_scorer.py` — `PostFilterProcessor`: filters low-performing reels out
  of an exported spreadsheet and ranks the rest by a weighted engagement
  score (views, like/comment rate, velocity — weights come from
  `config/scrapers.py`'s `ContentRankingWeights` / `WEIGHTS`). Also holds
  `VIEWS_THRESHOLD_DEFAULT` / `VIEWS_THRESHOLD_TODAY` cutoffs. Its internal
  module docstring still says `filter_reels.py`, a stale name from before
  this file was renamed.
- `instagram_public.py` — free, no-login Instagram scrape via Playwright
  (headless Chromium), no Apify actor involved: `fetch_profile_stats(username)`
  returns followers + the last N reels' views/likes/comments, and reports a
  banned/deleted/renamed account as an `error` instead of a live account with
  zero of everything (`_check_available`, EN + PL wording). **Always runs
  the browser in a subprocess** — see its docstring, RQ forks per job and
  fork+Playwright deadlocks. Tunables live in
  `config.settings.InstagramStatsSettings`; selectors/regexes stay here.
- `instagram_stats_job.py` — the RQ job that sweeps every Instagram account
  in the models roster and persists the result (`web/stores/instagram_stats.py`).
  Runs daily: `ensure_scheduled()` (called at worker boot) seeds one
  `enqueue_at`, and each sweep re-queues the next. Never use a thread for
  this — see the docstring.
- `post_exporter.py` — `PostExcelExporter`: writes `Reel`/`TikTokVideo`/
  `PostBase` lists to a formatted `.xlsx` (styled header, alternating row
  fills, sanitized sheet names).

# Who calls this

`web/routers/helpers/scraper.py` drives this pipeline from the web UI: pick a
scraper (`config.scrapers.SCRAPRES_REGISTRY`) -> run it via
`get_client_with_most_credits` + `run_actor` -> normalize items via
`Reel`/`TikTokVideo.from_apify` -> export via `PostExcelExporter` ->
optionally filter/rank via `PostFilterProcessor`.
