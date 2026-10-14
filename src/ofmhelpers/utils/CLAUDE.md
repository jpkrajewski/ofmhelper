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
- `video_probe.py` — `duration_s(path, timeout_s)`: container length via
  ffprobe; raises rather than guessing.

# Who calls this

`web/routers/downloads/clean_image.py` (metadata_cleaner);
`web/routers/task_helpers/uploads.py` `convert_heic_upload` (heic), applied
in `save_asset` and the clean-images/character-sheet routes;
`web/routers/image_tools/character_sheet.py` (character_sheet);
`web/routers/video_tools/uniqify.py` (uniqify);
`web/routers/generation/clone.py` (video_probe).
