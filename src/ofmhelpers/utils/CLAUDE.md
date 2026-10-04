# Module purpose

Standalone utilities that don't belong to a specific provider or pipeline.
Image metadata stripping, HEIC-to-JPEG conversion, character-sheet stitching.

# Module files

- `metadata_cleaner.py` — `clean_metadata(path)`: strips EXIF/metadata from
  every supported image in a folder, renaming each to `IMG_XXXX.png` in
  place (continues the existing numbering).
- `heic.py` — `heic_to_jpeg(stream)`: decodes an iPhone HEIC/HEIF photo
  (pillow-heif) to a quality-100, 4:4:4 JPEG, keeping ICC + EXIF.
- `character_sheet.py` — `combine_side_by_side(sources, dest)`: panels left
  to right, no gap, each scaled up to the tallest height (LANCZOS), PNG.

# Who calls this

`web/routers/downloads/clean_image.py` (metadata_cleaner);
`web/routers/task_helpers/uploads.py` `convert_heic_upload` (heic), applied
in `save_asset` and the clean-images/character-sheet routes;
`web/routers/image_tools/character_sheet.py` (character_sheet).
