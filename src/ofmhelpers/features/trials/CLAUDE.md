# features/trials

The page and its jobs: `web/routers/CLAUDE.md` (/trials).

## Order of the cards (owner 2026-11-15: "why are done trials showing at the end")
- **Made copies come first**, newest made first. `ReelState.made_at` is set by `mark_made`; a copy from before it sorts by `posted_at`. A copy made again (Regenerate, a caption) goes back to the top.
- **Then** what Generate is still making (`pending-<key>`), **then** the reels it could not make (`failed-<key>`: the file name and the short reason, never the exception).
- One order everywhere: `FolderView.made` / `pending` / `failed`, the page render (`_trials_cards.html`), `live()`, `GET /cards`.
- `trials.js` `order()` moves existing cards into the order `live` gives. It never moves a card in use: moving a playing video pauses it.

## One media worker, shared fairly (prod 2026-11-15)
- Why: on prod an upload waited 1585-1936 s and a 5-reel Generate ran 2601 s as one RQ job for every reel, with everything else queued behind it.
- **Generate gives the worker back after each reel when someone waits** (`tasks.Folder.make_trials`, `_someone_waits` = `JobQueue(MEDIA).line().waiting`). It returns `runner.Requeued`, and `run_job` puts the same job at the end of the same line with the same time limit.
  - The row stays `running`, and the page says "In queue · N ahead".
  - It goes on where it stopped: copies already made are skipped, and it reads POSTED once more.
  - Two Generates take turns, one reel each.
- **What a person waits for goes to the front** (`_Claim.launch(at_front=True)`): Regenerate, Apply caption, and an upload (one that has to make copies again goes on the media queue). Generate, its restart and its requeue join at the back. `at_front` puts the newest urgent job first among several.
- **Every job's row holds what it ran with** (`params.args`: the kwargs but the Drive token), so a job runs again from its row: `folder.replay_run(job)` + `folder.RUNS[label]` (`features/jobs/replay.py`).

## One copy, measured (dev 2026-11-15, the media worker's 4 CPUs, a 5.3 s 2160x3840 60 fps H.264 POSTED reel)
~159 s total (~28 s per second of video): master (x264 veryfast crf 10, at the source's 60 fps) 55.6, recorder (x265 `fast`, HEVC Main10 HLG 30 fps 24 Mb/s) 76.4, the rest 27.

A master at 30 fps instead of the source's was measured 2026-11-16 (same reel and seed, A/B twice on dev): no saving beyond the noise, and the copy changes (149 frames instead of 148, SSIM Y 0.93 between the two copies). Not applied. 116 of dev's 122 POSTED sources are 59.94 fps. The recorder's x265 is the biggest step; a faster preset there would lower quality at the same 24 Mb/s, so it is the owner's call.
