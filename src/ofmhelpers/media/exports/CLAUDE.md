# exports

How a finished video is exported. Pages show exports as one dropdown and each `ExportOptions` field as a switch (`mirror`); jobs store the name + switches.
- New export kind = one `Export` subclass in `_build`. New phone = new `recorder/<model>/profile.json`.
- New switch = one `ExportOptions` field, honoured by every `render` (file name gets `_<field>`); `original` with any switch on re-encodes, else returns the file untouched.
