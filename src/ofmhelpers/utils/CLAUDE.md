# Module purpose

Grab-bag of standalone utilities that don't belong to a specific provider or
pipeline: image metadata stripping, profile-name normalization across
platforms, an audio DSP effect chain, and a spreadsheet-to-dict loader.

# Module files

- `metadata_cleaner.py` — `clean_metadata(path)`: strips EXIF/metadata from
  every supported image in a folder, renaming each to `IMG_XXXX.png` in
  place (continues the existing numbering).
- `profile_loader.py` — normalizes a raw profile reference (URL, `@handle`,
  or bare username) down to a plain username, per platform.
  `PlatformNormalizer` (ABC) + concrete `InstagramNormalizer` /
  `TikTokNormalizer` / `XNormalizer` / `ThreadsNormalizer` /
  `RedditNormalizer`; `ProfileNormalizer` picks the right one by matching the
  URL's host; `ProfileLoader` / `normalize_profiles_names(profiles)` is the
  main entry point for a list of raw profile strings.
- `radio_comms_fx.py` — CLI + library for turning clean TTS audio into
  crunchy CoD/CS-style radio comms (bandpass, distortion, bitcrush, static,
  compression, tremolo dropout — see `PRESETS`). `process(audio, sr,
  preset_name, ...)` / `process_file(...)` / `generate_variations(...)` are
  the main entry points; runnable standalone (`python radio_comms_fx.py
  input.wav output.wav --preset cod_clean`, or `--batch` for a whole folder).
- `sheets_to_columns.py` — `sheets_columns_to_keys(path)`: reads an uploaded
  profiles `.xlsx` with one column per scraper (header names must match
  `config.scrapers.Scrapers` enum values exactly) and returns a dict keyed by
  that enum, each holding the column's non-empty values.

# Who calls this

`web/routers/helpers/radio_comms.py` (radio_comms_fx), `web/routers/downloads/clean_image.py`
(metadata_cleaner), `web/routers/helpers/scraper.py` (sheets_to_columns,
profile_loader).
