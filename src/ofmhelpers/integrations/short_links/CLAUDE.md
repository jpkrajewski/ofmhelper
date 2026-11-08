# links

Short links on link providers (Oopsie, Bouncy), provider-agnostic: `/links` (`web/routers/tracking/links.py`) lists and makes them, `features/links/sync.py` syncs/makes/deletes on the worker, `/accounts` shows each Instagram account's links (clicks + CTR) next to its phones.

- `providers.json` — per provider: label, `base_url`, key prefix, short-link `domains` (default first; Bouncy = bouncy.ai + its 42 system domains, all "premium" = paid plan, ours is Growth), `slug` rules (`invalid` char-run regex, min/max length, `unique_per` provider|domain), `min_interval_s` between calls (Oopsie 60/min), `stats_batch`, `max_history_days`, `max_span_days` (longest analytics range still answered in daily buckets), Bouncy `behavior`.
- `targets.json` — pages a link sends to (OnlyFans, Fanvue, Stacked): `hosts` (subdomains too), `prefix` (what the Generate dialog prefills: `https://www.stacked.com/@`, ...), `profile` (regex the URL path must match: a profile name, then anything -- Stacked needs the `@`). `Target.problem(url)` is the one check (server); the dialog runs the same pattern as you type. New target = one entry.
- `models.py` — `ProviderInfo`, `SlugRules.make(handle)`/`numbered(slug, n)`, `Target.matches`, `NewLink`, `RemoteLink` (`adult`: None = provider does not say), `DayStat`, `Usage`.
- `errors.py` — `LinkError` (message shown on /links as is), `AuthError` (401), `QuotaError` (402 / Bouncy 403 `TOTAL_LINK_LIMIT_REACHED`), `RateLimitedError` (429), `SlugTakenError`.
- `providers/base.py` — `LinkProvider` ABC: `create` (always 18+ gated: a link whose gate cannot be set is deleted again and the call fails), `list_links`, `daily(ids, start, end)`, `hourly(ids, day)` (one UTC day by the hour, `HourStat`: what the Telegram `money` answer reads live, `features/reports/hours.py`, 2026-11-08), `enable_adult`, `delete`, `usage`; `_call` (throttle, timeouts, status -> error mapping, through `utils.outbound` so the fakes answer under `OFM_FAKE_EXTERNALS`). `default_key()` = `settings.links.admin_key(key)` (`OOPSIE_API_KEY_ADMIN`, `BOUNCY_API_KEY_ADMIN`).
- `providers/oopsie.py`, `providers/bouncy.py`; registry `providers/__init__.py` `_PROVIDER_TYPES`. **New provider** = file + class in `_PROVIDER_TYPES` + `providers.json` entry + a wire fake in `fake.py` + its contract test rows. Never rename a key in use (stored on every link and in `user_api_keys`).
- `fake.py` — `FakeOopsie`/`FakeBouncy` at the wire (hosts `oopsie.bio`, `api.bouncy.ai`); only `OOPSIE_FAKE_KEY`/`BOUNCY_FAKE_KEY`; quotas (`limit`, Bouncy `active_limit`), `traffic[(id, day)] = (views, clicks)`, `set_hour_traffic(id, hour, ...)` (a one-day range answers by the hour, as both services do; a day seeded only as a whole sits in its first hour), Bouncy `adult` = ids with the gate forced.

Keys: a user's own (/account, `user_api_keys`, encrypted) always wins; an admin with none runs on the .env key; a user never gets it (`stores/short_links.api_key`). Saving a link key on /account syncs at once.

Storage (`db/models.py`, alembic c3d4e5f6a7b9): `short_links` (owner, provider, `remote_id` NULL while being made, slug/domain unique per owner+provider, url, destination, `target`, `creator_id`, `account_id` = the Instagram account it bridges from, `status` creating|active|inactive|failed|deleting, `adult`, `error`), `short_link_days` (link, UTC day, views, clicks; a sync rewrites from its first day on), `link_syncs` (owner+provider: `synced_at`, `claimed_at`, `error`, `usage` JSON). Link reads are uncached (they join creators/accounts that /accounts writes); `syncs()` is cached.

Sync (every `OFM_LINKS_SYNC_INTERVAL_S` = 3 h, per (user, provider) with a key, atomic claim like account_tracker; `POST /links/sync`, after every create/delete job): usage, list, daily numbers of `history_days` (max period, >= 30, capped by the provider). A remote link matches its row by remote id, else by (domain, slug) -- heals a create job killed after the provider made the link; new ones are imported, put on the owner's Instagram account whose handle makes that slug (with its creator); rows the provider no longer lists are deleted; "creating" rows older than `OFM_LINKS_CREATE_TTL_S` fail ("Interrupted").

Generate (`POST /links/generate`) is refused (400) when the batch exceeds `Usage.room` as the last sync saw it: free total slots and, for Bouncy, free active slots (past 5 active Bouncy makes links inactive = dead; dialog option shows "(N left)", JS checks the count too). A provider with no room, e.g. a full plan, (dialog: "(plan full)", disabled) -- the provider would refuse every link anyway (2026-10-22: an Oopsie attempt at 50/50 failed with `Plan limit reached (50/50)`). Then: model (an existing creator) + provider + domain + target + target link (`Target.problem`: host + profile path) + 1..`OFM_LINKS_MAX_GENERATE` of the user's Instagram accounts (the dialog pre-picks the model's). Slug = handle by the provider's rules, numbered ("-2") past the owner's own links, and again on the provider's "taken". Auth/quota/rate-limit errors fail the rest of the batch with the provider's words.

## Oopsie (verified 2026-10-22, reference: user-supplied `oopsie-api-reference.md`)
- `https://oopsie.bio`, header `x-api-key: gmp_live_…`, 60 req/min per key, envelope `{success, data|error}`. Workspace keys see only own pages.
- A link = a page. Ours: `POST /api/pages` `{slug, display_name, links: [], is_active, theme: {preferred_public_host, page_type: "direct", destination_url, age_gate: true, deeplink_escape: true, traffic_source: "instagram"}}` -> 201; 409 slug taken (slugs are provider-wide), 402 plan limit. Our account's 50 existing pages are all direct deeplinks with `age_gate: true` (OnlyFans, Stacked). Plan Pro = 50 pages, **50/50 used on 2026-10-22**: making more fails with 402 until pages are deleted or the plan grows.
- `PATCH /api/pages/:slug` replaces `theme` whole: `enable_adult` GETs it first.
- Daily numbers: `POST /api/analytics/activity` `{range: "custom", from, to, tz: "UTC", scope: "pages", scopeValue: "a,b", breakdown: "slug"}` -> `perSlug {labels: ["MM-DD"...], series: [{slug, views[], clicks[]}]}` (200 slugs per call, 93 days max). For direct deeplinks clicks == views (the redirect counts as the click). A one-day range (`from` = `to`, also `range: today|yesterday`) answers **by the hour**: 24 labels `"00:00"`..`"23:00"` in `tz` (checked on the real API 2026-11-08: near live, a click counted within the minute; `meta.source: durable-v2`). `hourly` reads it.
- `GET /api/account/usage` -> `pages_used`, `effective_limit`, `is_unlimited`.

## Bouncy (verified 2026-10-22, https://docs.bouncy.ai, index https://docs.bouncy.ai/llms.txt)
- `https://api.bouncy.ai/v1`, `Authorization: Bearer bcy_live_pk_…`, Growth plan: 1,000 req/h, 10,000/day. Our plan: 10 links total, 5 active (4/3 used on 2026-10-22); over the active limit a link is made **inactive**.
- `POST /links` `{url, slug, domain, behavior}`; taken slug -> 400 "Custom slug or domain already taken"; total limit -> 403 `code: TOTAL_LINK_LIMIT_REACHED`. `GET /links?page&limit<=100&type=deeplink`. Update is **PUT** `/links/{id}` (PATCH is 404); its response lists `updatedFields` (unknown fields silently ignored). Delete `/links/{id}` (id = slug on bouncy.ai, `domain__slug` elsewhere).
- **18+ gate**: not in the REST docs, and not accepted on create. Bouncy's MCP server (`https://mcp.bouncy.ai/mcp`, same API key as Bearer; tools `bouncy_create_deeplink` / `bouncy_update_deeplink` have `adult_warning`) writes `forceAdultWarning` + `adultContentCheckpoint`; REST `PUT /links/{id}` with both returns them in `updatedFields`, and the link's page then shows "Age verification required" (checked on a throwaway link, deleted). GET never returns the flags, so a listed link's gate is unknown (None). Bouncy also gates destinations it knows as adult (OnlyFans, Fansly) automatically; Fanvue/Stacked need the forced flag. The live contract test re-checks the rendered page.
- Analytics: `GET /analytics/links/{id}?from&to` (not `/analytics/{id}`; `/analytics/summary` is 404 despite the docs) -> `dailyViews [{date, views, clicks, adultClicks}]`. Buckets: one day -> hourly ("YYYY-MM-DD HH"), up to ~60 days daily, 90 days **monthly** ("YYYY-MM") -- hence `max_span_days` 31 and per-day summing. Clicks = 18+ confirmations that went through. One call per link per 31 days: a 90-day sync costs 3 calls/link. A deleted link's analytics answer 404 "Link or website not found": `daily` leaves that link out (logged), since one deleted between the list and the numbers failed the whole sync (2026-10-24).
- `GET /account/limits` -> `links {active, total, activeLimit, totalLimit}` (-1 = unlimited).

## Own bridge page (research 2026-11-07, nothing built yet)
What an Oopsie direct link is, read from `oopsie.bio/danywalking` (curl, 6 user agents):
- One static HTML page, no third-party scripts. Destination sits URL-encoded in the page source (`var dest = "https%3A%2F%2Fwww.stacked.com%2F%40…"`); same bytes for Safari, desktop, Instagram iOS/Android and `facebookexternalhit` -- no crawler cloaking on this page. Headers: `x-robots-tag: noindex, nofollow, noarchive`, `referrer-policy: no-referrer`, `cache-control: no-store`, Cloudflare in front.
- Server sniffs the user agent: Facebook app (`FBAN/FBIOS`) gets `x-gmp-webview: blocked`, an "Open in your browser" page that re-opens the bridge URL itself (`?_oopsie_external=1`) via `x-safari-https://…` (iOS >= 17) or `intent://host/path#Intent;scheme=https;S.browser_fallback_url=…;end` (Android), auto after 50 ms; the destination never loads in-app. Everyone else gets `x-gmp-webview: age-gate`.
- Age gate "Continue (18+)": `navigator.sendBeacon('/api/track-direct', {slug, referrer})` (beacon survives Instagram tearing the webview down), then Instagram -> `instagram://extbrowser/?url=<dest>`, X/Telegram -> the x-safari/intent escape, else a plain `location.href`. After 1.5 s a fallback shows "Tap ⋯ -> Open in external browser" + a plain link.
- `instagram://extbrowser` is undocumented; a vendor (https://linkscale.to/academy/instagram-deeplink-back-linkscale) says Meta stopped automatic hand-offs on 2026-03-18 and only part of iPhone traffic escapes now. Unverified; Oopsie still ships it.

Prices (2026-11-07): Oopsie (https://oopsie.bio, signup on Telegram, crypto only) Free 5 links; Plus 20 = $10/30 d ($0.50/link); Pro 50 = $20 ($0.40); Elite 100 = $30 ($0.30); Business 200 = $50 ($0.25). Bouncy (https://help.bouncy.ai/en/articles/13794064) Growth $35/mo = 5 active links; Scaling $75 = 20; Dominance from $150 = 50, $450 = 300. URLert rates oopsie.bio 0/100, domain ~1 month old (https://www.urlert.com/domains/oopsie.bio).
What they see: every click (slug, referrer, IP, UA via Cloudflare), the whole roster (model -> Instagram account -> OnlyFans/Stacked page) and they own the redirect: their outage, ban of their shared domain or a swapped destination hits every link at once.
Bouncy's "Non-Meta" bounce mode sends Meta visitors to the Bouncy homepage instead of the destination (https://help.bouncy.ai/en/articles/13794074). Meta bans Pages for cloaking, i.e. showing its review systems a different destination than users (https://about.fb.com/ltam/news/2017/08/abordando-el-cloaking-para-que-las-personas-vean-mas-posts-autenticos/).

## Self — our own bridge pages (built 2026-11-08)
A third provider, `self` (first in the picker), is our own bridge served from our
server on our own domains, counted from our own click log. It is a `LinkProvider`
like Oopsie/Bouncy so /links, the sync and the reports treat it the same, but
every method reads/writes our DB -- no HTTP, no API key, no `_call`.
- `providers/self_host.py` `SelfProvider`: `local_key(user_id)` returns `str(user_id)`
  (base returns None), so `stores/short_links.api_key` hands the owner id in as the
  "key" and every user is always keyed (no /account key, `has_key` always True).
  `create` mints the row live + 18+ gated (remote_id = `domain/slug`), `list_links`
  = our active/inactive `provider="self"` rows, `daily`/`hourly` aggregate the click
  log, `delete`/`enable_adult` are no-ops (the row is marked "deleting"/already gated;
  the next sync drops it). `usage.limit` = `OFM_LINKS_SELF_LIMIT` (None = unlimited).
- `providers.json` `self`: empty `domains` (they are ours, added at runtime); the
  Generate dialog fills them live (`routers/tracking/links._infos_with_self_domains`)
  and the route checks the chosen domain against `self_links.active_hosts(owner)`.
  `ProviderInfo.domains` is now `default_factory=list` and `default_domain` empty-safe;
  `GenerateLinks._known` skips the domain check when a provider has no static domains.
- New provider deviated from the usual steps: NO wire fake and it is NOT in the
  remote-HTTP contract test (`tests/contract` PROVIDERS stays `["oopsie","bouncy"]`);
  it has its own DB contract test `tests/integration/links/test_self_provider.py`.
- Tables (alembic d5e6f7a8b9c0, ORM `SelfLinkDomainRow`/`SelfLinkClickRow`):
  `self_link_domains` (host unique, owner_id NULL = shared, status), `self_link_clicks`
  (one row per hit: link_id, ts, kind view|click, ip raw, country/region/city/tz from
  Cloudflare, referrer, platform, device, ua; index (link_id, ts)). Owned by
  `db/repositories/self_links.py` + `db/stores/self_links.py` (kept free of
  `stores/short_links` and the provider registry to avoid an import cycle). Domain
  reads are `@cached` + bumped on domain writes; click writes never bump.
- The public bridge: `web/middleware/bridge.py` (outermost, before auth). On a host in
  `active_hosts()` it serves `GET /{slug}` (the 18+ gate `templates/bridge.html`,
  destination inlined, Instagram->`instagram://extbrowser`, X/Telegram/FB-app->
  x-safari/intent, else plain redirect; records a `view`, crawlers excluded) and
  `POST /api/track-direct` (records a `click` with geo from CF-Connecting-IP /
  CF-IPCountry / cf-ipcity / cf-region / cf-timezone). Byte-identical to crawlers (no
  cloaking). Any other host falls through to the app unchanged.
- `/self-links` (`routers/tracking/self_links.py`, "Self links" in More): manage the
  domains (add/disable/remove) and read the clicks over any time frame (`SelfRange`:
  `days` preset or `from`/`to`; a single day shown by the hour) -- totals, a bars chart,
  country/device/source/referrer breakdowns, per-link. Driven by `self_link_clicks`,
  so it is NOT limited to the /links `periods_days`. The links themselves still live on
  /links beside the others.
- Dev real-phone test: the `cloudflared` dev-profile service (docker-compose.dev.yml)
  gives a `*.trycloudflare.com` host; add it on /self-links, generate a self link, open
  on the phone. Prod needs the domains in Cloudflare + nginx passing CF-* headers (see
  CLAUDE.local.md).

### Self hardening (review 2026-11-08)
- Bridge lookup `active_self_link(host, slug)` is backed by a **partial index**
  `ix_short_links_bridge (domain, slug) WHERE provider='self' AND status='active'`
  (alembic e6f7a8b9c0d1) -- was a seq scan of every user's links on every hit.
- The bridge write path is **off the event loop**: `web/middleware/bridge.py` is
  pure ASGI (Starlette doesn't threadpool it), so the lookup, the click INSERT and
  the Jinja render all run via `run_in_threadpool`; one slow hit no longer stalls
  every request in the web process. For >100k hits/hour, batch/queue the inserts
  and add a daily rollup (`self_link_clicks_daily`); not needed below that.
- The public endpoints (`GET /{slug}` view, `POST /api/track-direct` click) have a
  **per-IP, per-minute cap** (`OFM_LINKS_BRIDGE_RATE_PER_MIN`, default 240; Redis,
  fail-open) -- the bridge runs outside `WriteRateLimitMiddleware`, so without this
  the beacon could flood `self_link_clicks` and poison stats (geo/referrer come
  from attacker-settable CF-*/body). Over the cap the page still serves; only the
  recording is skipped. Attacker-settable fields are length-capped in `_visitor`.
  Trust CF-* only once nginx sets real-IP from Cloudflare ranges (CLAUDE.local.md).
- `browser` is a **stored column** on `self_link_clicks` (set in `_visitor` like
  platform/device), not a per-row `ILIKE` CASE; the breakdown is a plain GROUP BY.
- Destination is re-checked `http(s)` in the bridge before render (defence in
  depth; it is HttpUrl + a known target host upstream already).
