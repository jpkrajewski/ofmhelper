# utils

Standalone media utilities.

- `metadata_cleaner.py` — `clean_metadata(path)`: strip image metadata, rename to `IMG_XXXX.png`.
- `heic.py` — `heic_to_jpeg(stream)`: q100 4:4:4 JPEG, keeps ICC + EXIF.
- `character_sheet.py` — `combine_side_by_side`: panels scaled to the tallest, PNG.
- `uniqify.py` — `generate_variants`: N jittered ffmpeg copies per `Strength`; `MetadataMode` strip or device tags.
- `capcut.py` — `apply_preset`: CapCut sliders -> ffmpeg filters (calibrated against a real export) + per-run jitter; output matches a CapCut 4K desktop export (H.264 Main L5.1, 59.94 fps, ~28-33 Mb/s, AAC 44.1k 192k, CapCut's tags; encoder key renamed in place, no x264 SEI). Grain before `exposure` (else colour static). `DEFAULT_PRESET`: scale 110, move_down 2, exposure -3, contrast 5, sharpen 15, particles 10, speed +3%, trim 0.3 s each end.
- `mov_atoms.py` — minimal QuickTime atom tree: `parse`, `Atom.to_bytes`, `read_moov`, `set_times`, `duration`/`set_duration`. Only moov/trak/mdia/minf/stbl/edts parsed; byte-identical round trip.
- `image_fit.py` — `fitted(src, dest_dir, ImageLimits)`: a shrunk JPEG copy (quality first, then pixels) when an image is over a provider's limits; kie.ai and Higgsfield uploads both use it (`settings.<provider>.ref_image_limits`, Wan 3.0: 8000 px, 20MB).
- `video_probe.py` — `duration_s`: container length via ffprobe.
- `proxy_pool.py` — `ProxyPool`: one job's proxies in turn (`start` offset), dead ones dropped, direct when none; used by `web/reel_watch.py` and `web/account_tracker.py`.
- `outbound.py` — every outside `requests` call: `mount(session)`, `get`, `head`. Under OFM_FAKE_EXTERNALS a `FakeAdapter` answers by host from the fakes (instagram, kie.ai, Higgsfield files); any other host is refused (`ConnectionError`), never reached.
- `fake_media.py` — real tiny media for the fakes: a playable 2 s mp4 (ffmpeg, cached in `OFM_FAKES_DIR`), images.
- `ffmpeg.py` — `binary`/`run`/`run_bytes` (raw stdout, e.g. pixels): the one ffmpeg/ffprobe runner (`RuntimeError` with the last 3 stderr lines, or "timed out after N s"; never the argv).

Callers: `clean_image.py`, `task_helpers/uploads.py` (heic), `character_sheet.py`, `uniqify.py`, `clone.py` (video_probe, capcut), `iphones/` (mov_atoms, ffmpeg), `web/reel_watch.py` + `web/account_tracker.py` (proxy_pool).
