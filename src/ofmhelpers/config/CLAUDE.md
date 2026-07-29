# Module purpose

Static configuration shared across the scraping pipeline: which scrapers
exist, their Apify actor input shapes, and how their ranked results are
weighted.

# Module files

- `scrapers.py` — `Scrapers` (StrEnum of scraper identifiers:
  `INSTAGRAM_PROFILES`, `TIKTOK_PROFILES`); `ContentRankingWeights` /
  `ScraperConfig` dataclasses; `prepare_raw_input_instagram_reel_scraper` /
  `prepare_raw_input_tiktok_reel_scraper` (build the raw dict payload for
  each platform's Apify actor); `SCRAPRES_REGISTRY` (dict mapping each
  `Scrapers` value to its actor id / config — note the "SCRAPRES" typo, kept
  for backwards compatibility with existing imports).

# Who calls this

`web/routers/helpers/scraper.py` imports `SCRAPRES_REGISTRY` and `Scrapers` to drive
the scraper picker; `scraping/apify.py` (actor input building);
`utils/sheets_to_columns.py` (reads the `Scrapers` enum to map spreadsheet
columns to scrapers); `scraping/post_scorer.py` (ranking weights).
