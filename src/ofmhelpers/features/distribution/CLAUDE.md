# distribution

/distribution (More menu): Pinterest accounts post a model's content by themselves. Owner 2026-11-18: "i just add my bulk of cookies on prod and it automatically chooses model with preset captions, their content and their links and uploads. i dont wanna look at it".

## Flow (owner 2026-11-18: "system just distributes the content, we just provide accounts")
1. **Add accounts:** upload any number of cookies.txt files; there is no model to pick. Whole-browser exports are fine, up to 20 MB each. For each file, `service.Distribution.add` does:
   - keeps only the Pinterest cookies and checks the login (`me`);
   - upserts the account (`distribution_channels`, keyed by owner + username: the same cookies again update it);
   - queues every model's content for it.
   A bad file is reported on the page; the rest go on.
2. **Content:** for every preset of the owner, the POSTED files (the /drive sweep's last read) and THREADS/<reel> pictures (read from Drive) of every Instagram account whose /accounts creator is the preset's `creator` (`sources.py`). Each queued file carries its model (`distribution_posts.preset`). Each file goes to each account at most once.
3. **Sweep** (`sweep.py`, every `OFM_DISTRIBUTION_CHECK_INTERVAL_S` 300):
   - fails posts cut off by a restart;
   - fills every running account's queue with every model's new files;
   - **on production only** (`OFM_DISTRIBUTION_SCHEDULED`; dev runs on a copy of prod's accounts and would pin everything twice) starts the next pin of each account whose `next_at` passed and that is not rate limited.
   - **Spacing:** a slot is 24 h / `OFM_DISTRIBUTION_PINS_PER_DAY` (15, owner 2026-11-18; forums say new accounts get throttled near it, and a rate limit makes that account wait), times a random 1 ± `OFM_DISTRIBUTION_JITTER` (0.3). A new account's first pin is at a random point of its first slot.
4. **Next file:** what none of the owner's accounts pinned yet comes first (accounts never all post the same reel), then queued before failed (tries left, `OFM_DISTRIBUTION_MAX_ATTEMPTS` 3), random within that. Every model's content goes out in proportion to how much there is.
   - At claim, the pin's text is drawn from the file's model and stored on the post: the title, a caption and the **model's one self link** (`links.py`: `https://<domain>/<slug>`, made on the model's first pin, shared by every account; owner: "links are per model not per account"), or "ig: <handle>" alone (below).
5. **Publish** (`publish.py`, job `pinterest`, default queue):
   - Drive download, then upload (video + first-frame cover).
   - The model's board on the account, found or made on first use, so an account ends up with one board per model.
   - The pin.
   - Rate limited: the post goes back in line and the account waits (`resume_at`).
   - Logged out: the account stops (`error`) until new cookies.
   - Anything else: the post is failed and retried later.

## Presets: how to make one (private: never in this public repo)
A preset is one model's settings: what a pin of its content carries. It lives in `distribution_presets`, one per owner and model; only its owner sees it. The repo is public, so preset files sit outside git, in `ops.local/presets/<key>.json` (gitignored).

1. **Model:** /accounts gives the model's Instagram accounts a creator name (e.g. "clark"). Their POSTED reels and THREADS pictures in Drive become the model's content.
2. **Link domain:** the model's link domain must be active on Self links (e.g. `go.ascendglobal.cash`).
3. **The JSON:** copy `preset.example.json` (in this folder) and fill it in:
   - `key`: lowercase, the preset's name (`clark`).
   - `creator`: the /accounts creator name.
   - `title` (≤ 100): the pin title, usually the model's name.
   - `board` (≤ 50): the board its pins go to, made on every account on first use.
   - `target` (a /links target: `stacked`, `onlyfans`, ...), `destination` (the model's page), `domain`, `slug`: the model's one self link is `https://<domain>/<slug>` → `destination` (numbered when the slug is taken).
   - `instagram`: the handles an "ig:" pin names.
   - `descriptions`: the captions, one drawn per pin. About 100, SFW (Pinterest bans suggestive content), each with a call to action ("more at the link"), ≤ 500 characters.
4. **Load it** on the server (the same key again replaces it):
   ```
   scp <file> <server>:/tmp/ && ssh <server> "cd <repo> && docker compose -f docker-compose.yml cp /tmp/<key>.json ofmhelpers:/tmp/<key>.json && docker compose -f docker-compose.yml exec -T ofmhelpers python -m ofmhelpers.features.distribution preset /tmp/<key>.json --owner <user id>"
   ```
   Check with `... presets --owner <id>`. From the next sweep round its content goes into every account's queue.

Made so far (2026-11-18): `dane` (Dane Walker Johnson, 6 Instagram accounts, 318 POSTED on prod) and `clark` (Clark Wayne, 4 accounts, 41 POSTED on prod: at 15 pins a day his accounts run out within days and the page says "nothing left to pin").

## What a pin carries (owner 2026-11-18: "50/50")
- `OFM_DISTRIBUTION_HANDLE_SHARE` (0.5) of the pins carry only `ig: <handle>` or `insta: <handle>` (a handle from `instagram`), with no caption and no link: people look the account up.
- The rest carry the model's `title`, a caption drawn at random and the account's self link.
- Each post row keeps its `title` / `description` / `link`; the report's `ig_pins` counts the first kind, and `views_per_pin` counts only linked pins.

## Sessions stay alive by themselves
- After every pin the cookies as Pinterest left them are saved over the stored ones (`publish._keep_session`, `PinterestClient.cookies_txt`). Pinterest renews its session on its answers, so the stored session is always the newest. A stopped (logged-out) account is never saved over.
- `cookies_expire_at` = the login cookie's expiry, kept at upload and at every save. The page warns 7 days before ("Cookies expire within 7 days, upload new ones: @x").
- Not verified yet: how long Pinterest keeps a session used from the server's IP, which differs from where the cookies were made. A logged-out account shows "Need new cookies (logged out): @x" at once.

## Monitoring
- **Log lines:** `pinterest @<account>: pinned <kind> <file> in <s> s -> <pin url>`, `... rate limited ..., account waits`, `... logged out, account stopped`, `distribution: @<account> (<model>) queued N new file(s)`, and one `distribution round: N account(s), N pin(s) started, N rate limited` per round. Every Pinterest refusal or odd answer: `pinterest <call> refused: ... (code, http)` / `answered <status> ..., not its JSON: <snippet>`.
- **Logfire:** span `pinterest pin @{account}` (account, channel_id, model, post_id, file, kind, source, attempt, pin_id) inside `job pinterest {job_id}`.
- **Page:** each account's state (Pinning / Paused / Rate limited / Stopped + why), today / pinned / queued / failed, next pin, link; the latest 30 pins with Open pin and Retry.

## Verified on dev 2026-11-18 (real account, real pins)
- 3 reels (98-262 s each) and 10 THREADS pictures (9-17 s each) pinned, all with the title, caption and go.ascendglobal.cash link. One TLS drop to S3 led to the upload retry.
- The bulk path re-added the same account: board found, `dane-1` made, 405 files of six Dane accounts queued. Pin now went through the real job: `IMG_7871.JPG` pinned in 16.8 s.
