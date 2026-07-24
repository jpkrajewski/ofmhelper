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
- `post_exporter.py` — `PostExcelExporter`: writes `Reel`/`TikTokVideo`/
  `PostBase` lists to a formatted `.xlsx` (styled header, alternating row
  fills, sanitized sheet names).

# Who calls this

`web/routers/scraper.py` drives this pipeline from the web UI: pick a
scraper (`config.scrapers.SCRAPRES_REGISTRY`) -> run it via
`get_client_with_most_credits` + `run_actor` -> normalize items via
`Reel`/`TikTokVideo.from_apify` -> export via `PostExcelExporter` ->
optionally filter/rank via `PostFilterProcessor`.
