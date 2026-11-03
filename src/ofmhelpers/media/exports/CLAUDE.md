# exports

How a finished video is exported. `exports()` = registry by name (`original`, `capcut`, one `DeviceExport` per `iphones/` profile); `get_export(name).render(src, ExportOptions) -> Path`. Pages show exports as one dropdown and each `ExportOptions` field as a switch (`mirror`); jobs store the name + switches. Settings: `settings.exports` (`default`, `mirror` start state, `timeout_s`, `device_timezone`, `device_place`).
- New export kind = one `Export` subclass in `_build`. New phone = new `iphones/<model>/profile.json`.
- New switch = one `ExportOptions` field, honoured by every `render` (file name gets `_<field>`); `original` with any switch on re-encodes, else returns the file untouched.
