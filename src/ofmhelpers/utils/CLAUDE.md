# utils

Standalone media utilities. Most of them now live in `media/` (not `utils/`); the paths below are the real ones.

- `media/capcut.py` — `apply_preset`: CapCut sliders -> ffmpeg filters (calibrated against a real export) + per-run jitter; output matches a CapCut 4K desktop export (H.264 Main L5.1, 59.94 fps, ~28-33 Mb/s, AAC 44.1k 192k, CapCut's tags; encoder key renamed in place, no x264 SEI). Grain before `exposure` (else colour static).
- `media/mov_atoms.py` — only moov/trak/mdia/minf/stbl/edts parsed; byte-identical round trip.
- `media/image_fit.py` — a shrunk JPEG copy (quality first, then pixels) when an image is over a provider's limits; kie.ai and Higgsfield uploads both use it (`settings.<provider>.ref_image_limits`, Wan 3.0: 8000 px, 20MB).
- `media/video_mp4.py` — `as_mp4`: H.264 video / AAC+MP3 audio copied (remux, rotation matrix kept); other video -> libx264 CRF 17 yuv420p (autorotated), HDR through `hdr.sdr_filters` + BT.709 tags; other audio -> AAC 192k; only `0:v:0` + `0:a:0` (a .mov's tmcd/data tracks dropped). Copy named `<stem>-<sha256(path:size:mtime)[:12]>.mp4`, written to a temp file then moved (never half a file), reused while the source is unchanged. Higgsfield uploads use it (prod 2026-11-02: an iPhone .mov failed /clone).
- `media/hdr.py` — `sdr_filters`: zscale HLG/PQ -> BT.709 SDR, SDR white 203 nits (BT.2408), "" for SDR.
- `utils/proxy_pool.py` — `ProxyPool`: one job's proxies in turn (`start` offset), dead ones dropped, direct when none.
- `integrations/outbound.py` (not in utils: its fakes table imports the integrations) — every outside `requests` call: `mount(session)`, `get`, `head`. Under OFM_FAKE_EXTERNALS a `FakeAdapter` answers by host from the fakes (instagram, kie.ai, Higgsfield files, Apify, ...); any other host is refused (`ConnectionError`), never reached.
- `media/ffmpeg.py` — the one ffmpeg/ffprobe runner (`RuntimeError` with the last 3 stderr lines, or "timed out after N s"; never the argv).
