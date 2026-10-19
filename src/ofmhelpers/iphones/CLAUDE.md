# iphones

Re-encode a video into a camera recording of a real iPhone model. One folder per model, `<model>/profile.json` (decoded from real recordings, shipped in the package), validated into `models.DeviceProfile`; `load_profile(name)` is cached. New model = new folder.

## Files
- `models.py` — `DeviceProfile`, `FrameTiming`/`TimingPlan`, `GeoPoint`/`Place` (location + jitter), `Recording` (end time in the phone's zone, length, location, seed).
- `atoms.py` — `mdta` meta (ordered keys, typed values, locales), Apple media/data handlers, `alis` dref, `tapt`, `vmhd`.
- `sample_entries.py` — stsd: video keeps ffmpeg's `hvcC`+`colr`; audio rebuilt (SoundDescription v1 + `wave`/`esds`; ffmpeg's AAC adds 3 SBR bytes no option removes).
- `sample_tables.py` — `cslg`, `sync` `sgpd`/`sbgp` from stts/ctts/stss; iPhone stbl order.
- `recorder.py` — `record(src, dst, profile, recording, timeout_s)`: ffmpeg encode from the profile, then moov rewritten; all tracks cut to the movie length via edit lists.

No package or Debian tool builds these boxes (searched 2026-10-19: pyqtfile, qtfaststart, mediameta, Bento4 mp4edit, MP4Box), so builders follow Apple's QuickTime spec, each tested against real bytes.

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
`tests/iphones/test_matches_real_recording.py` diffs every exiftool tag of a recorded synthetic clip against `fixtures/iphone17_img0063.exif.json` (full dump, location included). Excluded with reasons: file system, per-recording, location, tracks 3-8 (ffmpeg can't write APAC/mebx), the exiftool quirk on the real hvc1 entry (bytes checked directly).

Offered to pages through `ofmhelpers.exports` (one `DeviceExport` per profile; Mirror switch -> `Recording.mirror`); time zone and place in `settings.exports` (`OFM_EXPORT_DEVICE_TIMEZONE`, `OFM_EXPORT_DEVICE_PLACE`).
