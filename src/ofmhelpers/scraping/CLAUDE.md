# Module purpose

Two pipelines, both Apify-backed, both ending in a spreadsheet for review:

1. **Post ranking** — scrape and rank social posts (Instagram/TikTok reels).
2. **Lead hunting** — find small Instagram accounts promoting OnlyFans
   (`lead_hunt.py`), for creator outreach.

They share `apify.py` and the `models/` package and nothing else.

# Module files

- `apify.py` — `get_client_with_most_credits(api_keys)` picks whichever
  configured Apify API key has the most remaining monthly credit;
  `run_actor(client, actor_id, raw_input)` runs an actor and returns its
  dataset items as a list.
- `models/` — the post models, one file per platform, all Pydantic:
  `post.py` holds `PostBase` (username, url, timestamp, views/likes/comments,
  caption, duration, hashtags, `is_valid()`, and the shared
  `as_utc_datetime`) plus `Reel`; `tiktok.py` holds `TikTokVideo` and
  `TikTokAuthor` (nested author info). Each subclass owns its own
  `from_apify(item)` / `from_raw(a)` classmethod — Apify's actors are
  third-party and their key names drift, so the mapping belongs on the model
  rather than in whatever called the actor. Import from the package.
  `profile.py` and `lead.py` belong to the lead hunt, not the post pipeline:
  `InstagramProfile` (followers, bio, `external_urls` normalized from both
  actor shapes, `searchable_text`) and `Lead` (profile + `Confidence`
  CERTAIN/LIKELY + `onlyfans_url` + one-phrase `evidence` + `sort_key`).
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

## Lead hunt (OnlyFans creator outreach)

- `lead_hunt.py` — the pipeline and its operator CLI. `hunt(hashtags)` runs
  `discover_usernames` (hashtag actor -> owner usernames, deduped in
  first-seen order, capped at `max_profiles_per_run`) -> `fetch_profiles`
  (profile actor -> followers/bio/links) -> `qualify_all`. `qualify` applies
  `in_follower_band` (the sub-10k window, **exclusive** at the top) then the
  confidence ladder: direct onlyfans link > link resolved through an
  aggregator > the word alone. Run it with
  `python -m ofmhelpers.scraping.lead_hunt --hashtag <tag> --out leads.xlsx`;
  it prints a report, so `print()` is deliberate there.
- `of_links.py` — pure, network-free bio classification:
  `find_direct_of_url`, `find_aggregator_urls` (the `AGGREGATOR_HOSTS`
  list), `mentions_onlyfans`. The mention regex matches the initialism only
  in its dotted form (`O.F`) — a bare "OF" makes a lead of every ALL-CAPS
  bio and half the English ones.
- `aggregators.py` — the single networked step: fetch a linktr.ee-style page
  and look for an OnlyFans link on it. Bounded on every axis (timeout, read
  cap, one attempt, a pause between calls) and returns `None` on any
  failure, so a dead aggregator downgrades one lead to LIKELY rather than
  ending the run.
- `lead_exporter.py` — `LeadExcelExporter`: leads to a formatted `.xlsx`
  with both link columns hyperlinked, `.csv` fallback if the save fails.
  Its own exporter rather than a widened `PostExcelExporter` — a lead sheet
  shares none of the post grid's columns.

Tunables (follower band, actor ids, credit caps, aggregator timeouts) live
in `config.settings.LeadHuntSettings` / `settings.lead_hunt`; the host lists
and regexes stay in `of_links.py`, because those are code.

# Who calls this

`web/routers/helpers/scraper.py` drives this pipeline from the web UI: pick a
scraper (`config.scrapers.SCRAPRES_REGISTRY`) -> run it via
`get_client_with_most_credits` + `run_actor` -> normalize items via
`Reel`/`TikTokVideo.from_apify` -> export via `PostExcelExporter` ->
optionally filter/rank via `PostFilterProcessor`.
