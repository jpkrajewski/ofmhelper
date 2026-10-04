# Module purpose

The Instagram creator-lead hunt: find small Instagram accounts promoting
OnlyFans (`lead_hunt.py`), for creator outreach, ending in a spreadsheet for
review. Apify-backed.

# Module files

- `apify.py` — `get_client_with_most_credits(api_keys)` picks whichever
  configured Apify API key has the most remaining monthly credit;
  `run_actor(client, actor_id, raw_input)` runs an actor and returns its
  dataset items as a list.
- `models/` — Pydantic shapes, import from the package: `InstagramProfile`
  (followers, bio, `external_urls` normalized from both actor shapes,
  `searchable_text`) and `Lead` (profile + `Confidence` CERTAIN/LIKELY +
  `onlyfans_url` + one-phrase `evidence` + `sort_key`). Apify's actors are
  third-party and their key names drift, so the mapping belongs on the model.

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

Tunables (follower band, actor ids, credit caps, aggregator timeouts) live
in `config.settings.LeadHuntSettings` / `settings.lead_hunt`; the host lists
and regexes stay in `of_links.py`, because those are code.

# Who calls this

Nothing in the web app: `lead_hunt.py` is an operator CLI
(`python -m ofmhelpers.scraping.lead_hunt`). `scripts/check.py` also uses
`apify.py`.
