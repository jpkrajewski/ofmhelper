# viral

/viral's search: one Instagram profile's most played reels, through Apify's `apify/instagram-reel-scraper` (https://apify.com/apify/instagram-reel-scraper; input/output/pricing read from its build `actorDefinition`, 2026-10-31).

- `service.py` — `resultsLimit: OFM_VIRAL_SCAN` (200; **always set**: without it the actor reads the whole profile), pinned reels kept (often the most played), every paid add-on (`includeSharesCount`, `includeTranscript`, `includeDownloadedVideo`) explicitly off. Error items (the run itself succeeds): `not_found` -> "Profile not found: @x", `Restricted profile` (an age-restricted profile, shown only signed in; prod 2026-11-13 @bigboylazar) -> "Age-restricted profile @x: ...", anything else (`no_items` = "Empty or private data") -> "No reels on @x: private, empty or age-restricted". Videos are downloaded at once from Instagram's CDN (its URLs expire within days: never handed to the browser). One failing download is left out (logged), all failing = error.
- Actor items (`integrations/apify/instagram.py`): `videoPlayCount` is what Instagram shows; `videoViewCount` is deprecated by Instagram, only the fallback. `likesCount`/`commentsCount` -1 = hidden -> None.
- `media.py` — `videoUrl` is normally the progressive 720p mp4 with sound (`vts_vod_urlgen.clips.c2.720.baseline`); the actor's changelog: some reels exist only as separate video and audio streams, then `videoUrl` is picture only and `audioUrl` the sound (DASH HE-AAC) -- put back in.

Prices (Free plan): $0.0026 a reel + $0.001 a run -> a 200-reel search <= $0.52 (`settings.viral.max_usd`, the page header).

Web side: `web/routers/downloads/viral.py`.

Age-restricted profiles (2026-11-14): only the message is fixed. Reading one through the user's own Instagram cookies is not built: `integrations/instagram` `reels_logged_in` + `media_logged_in` list and resolve reels signed in, but the profile's numeric id comes only from `open_profile`, which is signed out. Whether the signed-in `/{username}/reels/` page carries `profile_id` for such a profile could not be checked on dev: the cookies saved there are a dead login (a public profile's page answered the `CAAIGLoginHomepage` page too). Needs a live login on dev first.
