# apify

Apify's REST API v2 (https://docs.apify.com/api/v2), only the calls the app makes, through `integrations.outbound` (no `apify-client` dependency; the fake answers at the wire).

- `client.py` — `ApifyClient(token)`: `remaining_usd()` (`GET /users/me/limits`: `limits.maxMonthlyUsageUsd - current.monthlyUsageUsd`), `run(actor, input, timeout_s)` (`POST /acts/{owner~name}/runs?timeout=` once, never retried: a lost answer would pay for a second run; then `GET /actor-runs/{id}?waitForFinish=60` until a final status), `items(run)` (`GET /datasets/{id}/items?clean=true`). Errors: 401 `ApifyAuthError` ("Wrong Apify API key"), 402/403 or an error type with "usage" `ApifyCreditError` ("No Apify credit left"), else `ApifyError`; messages are what a card shows. `by_credit(tokens)`: every readable account with its credit left, most first (unreadable ones logged and skipped); `richest(tokens)`: the first of them (none = `ApifyAuthError`).
- **Cost** (`models.py` `ActorRun.cost_usd`): a pay-per-event actor's charges reach `usageTotalUsd` only after the run ends (a real 20-reel run, 2026-10-31: $0.001 when it turned SUCCEEDED, $0.053 later), so `cost_usd` = `chargedEventCounts` x `pricingInfo.pricingPerEvent.actorChargeEvents[*].eventPriceUsd`, or `usageTotalUsd` when that is more.
- `fake.py` (`api.apify.com` under OFM_FAKE_EXTERNALS) bills like the real one incl. the lag above; `cut_short` = actors whose runs end ABORTED with half their items; private profile = the actor's `no_items` item, unknown = `not_found`; keys `BROKE_KEY` (402), `WRONG_KEY` (401).

Keys: `APIFY_API_KEYS` (comma-separated, `settings.viral.api_keys`; both prod accounts are Free plans, $5 a month each, hard stop when used up). The real half of the contract test runs under `live` (3 reels, under a cent).
