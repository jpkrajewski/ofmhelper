# recorder

Re-encode a video into a camera recording of a real iPhone model. One folder per model, `<model>/profile.json` (decoded from real recordings, shipped in the package), validated into `models.DeviceProfile`. New model = new folder. Exception: `iphone16` is adapted from the `iphone17` profile (model / lens / software strings only, `samples: 0`) — no iPhone 16 recording decoded yet; swap in real bytes when available. `iphone16` keeps the default `medium` preset: `fast` measured 2026-11-21 on a 6 s 1080p30 clip at 8.4 Mb/s vs a near-lossless record of the same clip: PSNR 43.27 -> 42.87 dB (-0.39), SSIM 0.9740 -> 0.9731, time 27-42 s -> 22-24 s. `iphone16_4k` = `iphone16` at 4K30 (2160x3840, level 5.1, 24 Mb/s: Apple's 170 MB/min for 4K30 HEVC, as its 60 MB/min matches the 1080p profile's 8.4 Mb/s) -- also a guess until a real 4K recording is decoded; `x265_preset` `fast` (profile field, default medium: same refs/SPS flags, ~2x quicker at 4K).

`sample_entries.py`: audio is rebuilt (SoundDescription v1 + `wave`/`esds`; ffmpeg's AAC adds 3 SBR bytes no option removes). No package or Debian tool builds these boxes (searched 2026-10-19: pyqtfile, qtfaststart, mediameta, Bento4 mp4edit, MP4Box), so builders follow Apple's QuickTime spec, each tested against real bytes.

## Findings (7 iPhone 17 Pro Max recordings, iOS 26.5.1, front camera)
- Keys: text = type 1; `full-frame-rate-playback-intent` type 21, 8 bytes, value 1 (ffprobe misreads 0); maker notes .74/.97 type 21, 4 bytes (6/20). Lens keys locale `US`/`eng` (0x555315C7), movie keys locale 0.
- Every track: hdlr vendor `appl`, data handler `alis` "Core Media Data Handler", dref `alis` flag 1, mdhd language `und` (0x55C4). vmhd mode 64, opcolor 32768.
- Video: HEVC Main 10 `hvc1` L120 Main tier, 1080x1920, HLG/BT.2020, B-frames 2, ~8.4 Mb/s, timescale 600, signed ctts + `cslg`, sync group NAL 21 (CRA), Dolby Vision 8.4.
- Timing (VFR): stts starts 80 (first frame = 4 slots), then 0-4 gaps of 40/60, then 20s with one 21 per ~250 frames, last frame 12-21. Audio: one run of whole 1024-sample packets. Chunks ~0.49 s.
- Audio: AAC LC 48k stereo, esds buffer 6144, max 192000, avg 0; plus 4-ch APAC; 4-5 `mebx` tracks.
- File: `ftyp qt` minor 0, `wide`, `mdat`, `moov` last; all tracks end with the movie.
- Per recording (not in the profile): `creationdate`, location, accuracy (2.05-2.66 m).

## ffmpeg gotchas
- SDR -> HLG must pass `npl=203` to zscale (profile `sdr_white_nits`): SDR white then lands on BT.2408 reference white (75% HLG, code 721). zscale's default (100) puts it at 616 and the video looks darker.
- `-fps_mode passthrough` needs `fps=` in x265 params (else 600 fps from the timebase, level 4 fails).
- `setts` bsf must pass `pts=PTS:dts=DTS` or it drops the ctts.
- x265 4.1 segfaults on `cra-nal=1` with B-frames -> keyframes are IDR (19); x265 sets no non-packed constraint flag.
- `-chunk_duration 370000` gives ~0.49 s chunks.

## Verification
`tests/unit/iphones/test_matches_real_recording.py` diffs every exiftool tag of a recorded synthetic clip against `tests/fixtures/iphones/iphone17_img0063.exif.json`. Excluded with reasons: file system, per-recording, location, tracks 3-8 (ffmpeg can't write APAC/mebx), the exiftool quirk on the real hvc1 entry (bytes checked directly).
