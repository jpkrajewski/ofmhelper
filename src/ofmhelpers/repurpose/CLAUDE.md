# repurpose

One video -> one repurposed copy, from JSON presets, plus a checker that measures how much a copy still matches its source. Exploring phase (2026-10-26): tests only where the user asked (the CapCut and iPhone ones below); otherwise the check is "every preset x device runs ffmpeg without crashing" (smoke script, see Verification).

## API (`repurpose.py`)
```python
from ofmhelpers.repurpose import Repurpose
from ofmhelpers.repurpose.checker import check

result = Repurpose("in.mp4", "out.mp4").apply_json_preset("hard").run()
(
    Repurpose(src, dst, seed=7, recorded_at=when)
    .apply_video_preset("extreme", special=True)  # + its video_special knobs
    .apply_audio_preset("low")
    .apply_device_preset("iphone17")
    .run()
)
report = check(
    "in.mp4", "out.mp4"
)  # CheckReport: looks_like_source, sounds_like_source
```
- `apply_video_preset(name, special=False)` / `apply_audio_preset(...)`: a preset's `video` / `audio` knobs (+ `video_special` / `audio_special`).
- `apply_json_preset(name, special=False)`: video + audio + the device the preset names (`device`, or `device_special` with special).
- `apply_device_preset(name)`: encode + tags. Default `strip`.
- A name is `presets/<name>.json` / `devices/<name>.json`, or any path to a `.json`.
- `recorded_at` (aware datetime): the copy's creation time; None = now.
- `run()` -> `RunResult`: output, source, device, `step` (our encode: argv + every drawn value + make-up gain; for a recorder device the master), `recording` (recorder devices), seconds. Same seed = same copy.

## Layout
- `presets/` — `low`, `medium`, `hard`, `extreme` (legacy uniqify MINIMAL..EXTREME ranges incl. its 2% boost + handheld; `*_special` = Duplyfi, the former Special switch; generated from the legacy values), `capcut` (utils.capcut DEFAULT_PRESET as Adjust sliders + its jitter, `capcut` device), `iphone17` (no edits, `iphone17` device), `stealth_wild` (the lab's pick: framing mix + vignette, pitch +-0.5 + flanger/phaser/vibrato, `strip`). `Preset` model in `__init__.py`.
- `devices/` — `strip` (legacy uniqify encode, default), `iphone_tags` (legacy device-metadata mode: tags only), `iphone17` (real iPhone 17 Pro Max recording by `ofmhelpers.iphones`; with knobs set they go into a `master` first), `master` (near-lossless, source size/rate), `capcut` (utils.capcut 4K export: tags, Main L5.1, 28 Mb/s, no SEI, `encoder` renamed, empty compressorname), `shuffled` (Duplyfi: random x264 preset/CRF/GOP/B-frames, fps near source, random audio bitrate). `Device` model: `VideoEncode` (size rule `max_width` | `short_side`, fps number | "source" | "near_source"), `AudioEncode`, `Container`, `Tags` ("{creation_time}" placeholder; "now" | "recent"), `Patches`, `Recorder` (profile, time zone, place).
- `transformations/` — `video.py` (`VideoSettings`, `VideoTransformer`: fixed filter order in its docstring), `adjust.py` (CapCut's Adjust panel, `VideoSettings.adjust`, in slider units; calibrated: exposure, contrast, sharpen, particles; the rest first guesses, see its docstring), `audio.py` (`AudioSettings`, `AudioTransformer`), `handheld.py`, `patterns.py`.
- `checker/` — tier 1, the user's notes in `__init__.py` applied: `frames.py` (copy 1 frame/s, source 10/s, picked on timestamps with `select` + `showinfo` times), `normalize.py` (flat frames left out; borders cropped, grey + equalized, blurred, 256x256; full + 75% centre-crop views; the 8 dihedral variants), `visual.py` (PDQ / pHash / dHash / wHash: source stores both views, copy queries both views x 8 rotations/flips, closest source frame per copy frame), `sequence.py` (matches fit to one line `source_t = scale x copy_t + offset`, scale 0.8-1.25, Hough vote; share of copy frames on it), `audio.py` (Chromaprint via ffmpeg's muxer, best offset), `check.py` (`check()`, `CheckReport`: looks like source = any hash / SSCD puts >= 50% of the copy's frames on one line, or TMK matches; a source's stored hashes kept per process, `lru_cache`), optional where installed (the lab pod, not the app image): `sscd.py` (Meta's SSCD descriptor, `OFM_SSCD_MODEL` TorchScript + torch, cosine >= 0.75, copy also mirrored, same in-order matching) and `tmk.py` (ThreatExchange TMK+PDQF tools in `OFM_TMK_DIR`, commit 295fd16e, whole-video level 1/2 >= 0.7).
- `lab/` — the knob search (see Lab).
- `values.py` — knob values: number, `[lo, hi]` (uniform; symmetric ranges cover both directions), list of words (one picked), chance 0..1.
- `probe.py` — `Source` (duration, size, audio, rotation, fps, `display_size`).
- `command.py` — argv from the drawn parts; `level.py` — volumedetect make-up gain; `container.py` — same-length byte patches; `runner.py` — ffmpeg runs returning the log / bytes.

New knob = one field on `VideoSettings`/`AudioSettings`/`AdjustSettings` + its filter; new device/preset = a JSON file.

## Web page
`/repurpose` (`web/routers/video_tools/repurpose.py`, More menu): one source video, every knob (read off `VideoSettings`/`AdjustSettings`/`AudioSettings` by `web/schemas/repurpose.py`: a new field shows up with no template edit; a video knob missing from `_VIDEO_GROUPS` lands in "Other"), preset (default `medium`, fills the knobs; Special lays `*_special` + `device_special` over them), device (`master` hidden), copies (<= `OFM_UNIQIFY_MAX_COPIES`). Download preset = the knobs + device as a preset JSON (`POST /repurpose/preset.json`). One RQ job per Generate (`job_timeout` = copies x `OFM_UNIQIFY_ENCODE_TIMEOUT_S`): per copy a fresh seed, `apply_json_preset(<job dir>/preset.json)`, then `checker.check` -> `RepurposeResult.score`. A done job is one card per copy: badges (Unique / Picture / Sound matches source) + one row per method, `<method> · video|audio  X%` = the share of the copy that method finds in the source (picture hashes: frames in order; Chromaprint: ~2 s windows), 50% or more = caught (`checker.CAUGHT_SHARE`); cards scored before 2026-10-26 04:30 have badges only. Recreate the only button (`_gallery_card.html` `actions` block); generation.js's live cards are swapped for `/repurpose/cards/{job}` (`static/js/repurpose.js`). Recreate restores video (reused, not re-uploaded), preset name, Special, knobs, device, copies.

## Lab (`lab/`, 2026-10-26)
Which knobs / mixes hide a copy from the checker while it stays nearly identical. Never runs in the app; a big CPU box:
- `space.py`: every knob with its cap (the "nearly identical" limits: speed +-6%, zoom <= 6%, tilt <= 1 deg, pitch +-0.5 semitone...; mirror and black-and-white left out), strength -> preset value; random throws may go to 1.5x a cap.
- `measure.py`: one copy (device `strip`) -> per-method % found (hashes, SSCD, TMK scaled so its 0.7 line sits at 50), Chromaprint %, VMAF against a reference with the copy's exact framing draws and none of its look (`VideoTransformer.from_draws`, same `-r` as the copy), sound change in dB (log-band spectrogram vs the source, best offset).
- `search.py`: calibration (cross pairs of the clips through SSCD / TMK) -> ablation (each knob alone at 1/3, 2/3, 1, 1.5 x cap, 2 clips) -> random throws -> Optuna NSGA-II per part (video: least caught at VMAF >= baseline - 3, VMAF counted no higher than the baseline; audio: least Chromaprint % and least change), seeded from the best throws; `--phases` runs a subset; results.jsonl appended as they land.
- `report.py`: knobs.csv, trials.csv, presets/stealth_{safe,balanced,wild}.json (video pick + audio pick), validation.csv (each preset on every clip x 2 seeds), examples/*.mp4 (source | copy), index.html.
- VMAF baseline: an untouched strip copy scores 94.9 (the 720p CRF 23 encode alone); sharpening raises VMAF to 98 (VMAF's known enhancement gain), hence the cap. Floors and picks are relative to the baseline.
- RunPod run (what worked, 2026-10-26): REST v2 `POST https://api.runpod.io/v2/pods` with `cpu {id: cpu5c, vcpuCount: 32}`, image `python:3.12-slim` (same Debian / ffmpeg 7.1.5 as prod), `ports ["22/tcp"]`, `env.PUBLIC_KEY`, `entrypoint bash -c "apt-get install openssh-server ffmpeg g++ curl xz-utils; authorized_keys from $PUBLIC_KEY; exec sshd -D"`; SSH host/port from `GET /v2/pods/{id}` -> `ssh.direct` (no sftp: copy with `tar | ssh`). On the pod: uv, `UV_PROJECT_ENVIRONMENT=/lab/venv uv sync --frozen --no-dev --group lab --no-install-project`, `uv pip install torch --index-url https://download.pytorch.org/whl/cpu`, the SSCD model `https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt`, BtbN's static ffmpeg (has libvmaf) for `OFM_LAB_VMAF_FFMPEG`, the TMK tools built in python:3.12-slim (`OFM_TMK_DIR`), `PYTHONPATH=src`; long jobs under `nohup` (an idle SSH session was reset mid-run). cpu5c x 32 = $1.12/h; ~1.2 measurements/s on single knobs, ~0.6/s on mixes, with 20 workers. Delete the pod when done (`DELETE /v2/pods/{id}`).

Lab results 2026-10-26 (full write-up: `reports/lab/run-2026-10-26/FINDINGS.md`, local only): nothing passed every detector. Each falls to a different edit: PDQ/dHash to small framing, pHash/wHash partly to bigger framing (stealth_wild), SSCD only to the blurred-border look (2 of 16 copies), Chromaprint to pitch ~0.73 semitone + chorus + room echo (0% on 6 of 8 clips). The lab missed the last two: pitch was capped at 0.5 and VMAF scored the border as damage (it compares against the unbordered frame). Next: one preset stacking border + stealth_wild framing + pitch ~0.7 + chorus + room echo.

## Findings (2026-10-26, ffmpeg 7.1)
- ffmpeg's `noise` seed is fixed by default: identical grain in every copy. Every copy here draws `all_seed`.
- Duplyfi's `aphaser` (in/out gain 0.4 x 0.74) costs ~10 dB, its chorus ~5 dB, `acompressor` at -30 dB ~11 dB: `match_level` measures copy vs source (same trimmed window) and adds it back before the limiter. Stereo reel: within 0.4 dB. A mono source comes out ~3 dB lower: `-ac 2` spreads it at -3 dB per channel (legacy uniqify too).
- `perspective` smears edge pixels instead of filling black; the handheld overscan keeps every edge out of frame.
- `unsharp` is positional `lx:ly:la:cx:cy:ca`; `5:5=0.2` is a parse error.
- Crop/scale round trips lose 1-6 px to even-rounding: the chain ends at the exact output size (numbers, not `iw`).
- ffmpeg 7.1 (Debian) has the `chromaprint` muxer: no fpcalc/pyacoustid needed. `pdqhash` 0.2.8 has no Linux wheel: the Dockerfile's build and test stages install `g++` to build it (runtime copies the compiled venv; libstdc++ is in slim).
- Frame sampling: ffmpeg's `fps` filter picks somewhere inside each slot, so a copy and its source sampled at different rates landed 0.1-0.4 s apart; a plain no-knob re-encode then read PDQ 34-104 bits and pHash 67% on fast handheld reels. Now: `select` on timestamps (first frame at/after each step) + `showinfo` times; the plain re-encode reads 100% on every method. Before the fix, a 0.1 s trim alone "beat" PDQ / dHash (0-17%): a sampling artefact, not a real weakness.
- Checker calibration (2026-10-26, scratchpad `calibrate.py` + `wcal.py`; source 10/s, copy 1/s): 16 unrelated reels, 236 ordered pairs, vs VID_6932 and 10 copies. Two template remakes left out as not unrelated: VID_7646 / VID_6738 and VID_7319 / VID_6932 (same spot, framing and sound, another girl; they match on several hashes and on Chromaprint). Per hash the highest threshold with no unrelated pair matching >= 50% in order, minus one step: PDQ 64 (clean to 91), pHash 12 (clean to 14, VID_3217<-1.mp4 at 16), dHash 6 (clean to 8, IMG_2967<-1.mp4 at 10), wHash 6 (clean to 7, VID_6683<-1.mp4 at 8). Copies caught at those: phash 12 finds low/medium/capcut/iphone17 >= 90%, hard 83%, extreme 67%, hard_sp 82%, extreme_sp 40%, hard_sp + iphone17 40%.
- SSCD / TMK false-positive check (lab pod, 8 test clips, 56 ordered pairs): SSCD median cosine at most 0.47 (threshold 0.75), 0 pairs matched; TMK min(level1, level2) at most 0.42 (threshold 0.7). TMK on a 6 s reel: plain re-encode 0.998, trim 0.3 s + crop 5% + saturation 0.963.
- PDQ is brittle to our handheld edits: `low` (tilt/shake/drift, pHash 5 bits) is ~52 bits on raw frames, 58 normalized; Meta's 31 catches only a plain re-encode (`iphone17`, 2 bits). pHash/dHash carry the verdict for light presets.
- Verdicts at those thresholds (copies of VID_6932): looks like source = low, medium, hard, extreme, low_sp, extreme_sp, iphone17; picture passes = hard_sp, capcut, hard_sp + iphone17. Sound matches on all but hard_sp + iphone17 (0.68). Old checker (pHash 10, any order, mirrors only) passed hard/extreme pictures: the new one is stricter.
- Audio as windows (16 items ~2 s, each may drift +-16 items off the track's best offset, >= 0.7 bits agree = found): 15 reels with sound, 210 ordered pairs: 0% everywhere except two pairs at 100% that really share a sound (VID_7646/VID_6738 0.94 whole-track, VID_7319/VID_6932 0.89). Copies of VID_6932: low/medium/hard/capcut/iphone17 100%, extreme 50%, low_sp 25%, hard_sp + iphone17 33%, hard_sp/extreme_sp 0%. Sounds like source = windows >= 50% (was whole-track >= 0.7).
- Cost: `check()` ~2-4 s per 12 s reel copy (hashing 16 query variants x 4 hashes per frame).
- Cost per copy, 12 s 1080x1920 reel: hard 8.9 s, hard+special 14.6 s, extreme+special 13.1 s, capcut (4K) 14.8 s.

## Not ported yet
- Duplyfi's batch logic: codewords (corner/grade/chroma/fps spread across copies), obvious effects rotated across copies, cuts spread evenly. Here each copy picks at random.
- Duplyfi's phone imitation ("US Metadata": 24-model catalog with each phone's resolution/fps/bitrate, GPS in a US city, recent creation time, moov rewrite). Our `iphone17` device does the iPhone side more faithfully.
- TikFusion's hue rotation (`hue=h=` +-5..20 degrees).
- Checker: per-scene sampling, CLIP + faiss, an AugLy false-positive set (calibration used 16 own reels + 8 test clips); SSCD/TMK in the app image (torch ~1 GB).

## Legacy
`utils/uniqify.py` (+ the /uniqify page) is the old path, back to its pre-2026-10-26 state (only the `unsharp` syntax fix kept). `utils/capcut.py` stays the /clone CapCut export and must not change. Nothing of this package lives in `utils/`.

## Verification
- Smoke (scratchpad script, 2026-10-26): every preset x special x device on a 720x1280 clip with sound, plus hard+special x every device and capcut on a 25 fps landscape clip, a silent clip and a sideways-stored phone clip: 51/51 ran.
- Page (2026-10-26, dev on prod data): curl run + Chromium: preset fill, Special merge, device switch, Download preset, Generate -> 2 scored cards (hard+special shuffled: one Unique, one "Sound matches"; low strip: both match), Recreate, no JS errors. No tests for the page yet (exploring phase, user's call).
- Tests: `tests/unit/utils/test_capcut.py` (the three apply_preset tests) and `tests/unit/iphones/test_recorder.py` + `test_matches_real_recording.py` run the same asserts on `Repurpose(...).apply_json_preset("capcut" / "iphone17")` below the original ones.
