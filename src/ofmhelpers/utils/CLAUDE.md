# Module purpose

Standalone utilities that don't belong to a specific provider or pipeline.
Image metadata stripping, HEIC-to-JPEG conversion, character-sheet stitching,
video uniqify.

# Module files

- `metadata_cleaner.py` — `clean_metadata(path)`: strips EXIF/metadata from
  every supported image in a folder, renaming each to `IMG_XXXX.png` in
  place (continues the existing numbering).
- `heic.py` — `heic_to_jpeg(stream)`: decodes an iPhone HEIC/HEIF photo
  (pillow-heif) to a quality-100, 4:4:4 JPEG, keeping ICC + EXIF.
- `character_sheet.py` — `combine_side_by_side(sources, dest)`: panels left
  to right, no gap, each scaled up to the tallest height (LANCZOS), PNG.
- `uniqify.py` — `generate_variants(...)`: one video re-encoded (ffmpeg) into
  N copies, each with speed/zoom/colour/grain/mirror/trim/volume jitter drawn
  from a `Strength` preset; `MetadataMode` strip or phone-style device tags.
  Port of the standalone `captions/` tool (its "keep original EXIF" mode
  needed exiftool, which the image lacks, so it was not ported).
- `capcut.py` — `apply_preset(src, dst, preset, timeout_s, seed)`: one
  ffprobe + one ffmpeg pass. CapCut sliders (scale/move_down/exposure/
  contrast/sharpen/particles) mapped to ffmpeg filters on a fixed linear
  scale (approximate, CapCut is closed), plus speed % and head/tail trims,
  plus a random per-run jitter in uniqify's MEDIUM ranges (saturation/
  contrast/brightness/zoom/grain/volume; own copy, no uniqify import).
  Slider scales calibrated against one real CapCut export of the same clip
  (tone within ~2 levels, matching sharpness and grain without the jitter).
  Filter order matters: grain goes in before `exposure`, whose float-RGB
  output `noise` would turn into colour static. Output matches a CapCut 4K
  desktop export: H.264 Main `avc1` level 5.1, short side 2160, 59.94 fps
  ~28-33 Mb/s no B-frames bt709, AAC 44.1 kHz stereo 192k, CapCut's
  container tags in CapCut's order (`bitrate=55000000`,
  `encoder=Lavf61.1.100` written under a placeholder key, renamed in
  place), no stream `encoder`, no encoder version strings in the video
  (x264 SEI dropped) or audio (bitexact AAC) data, source tags (incl. AI
  labels) dropped. `DEFAULT_PRESET` = scale 110, move_down 2, exposure -3,
  contrast 5, sharpen 15, particles 10, speed +3%, trim 0.3 s each end.
- `video_probe.py` — `duration_s(path, timeout_s)`: container length via
  ffprobe; raises rather than guessing.

# Who calls this

`web/routers/downloads/clean_image.py` (metadata_cleaner);
`web/routers/task_helpers/uploads.py` `convert_heic_upload` (heic), applied
in `save_asset` and the clean-images/character-sheet routes;
`web/routers/image_tools/character_sheet.py` (character_sheet);
`web/routers/video_tools/uniqify.py` (uniqify);
`web/routers/generation/clone.py` (video_probe, capcut).
