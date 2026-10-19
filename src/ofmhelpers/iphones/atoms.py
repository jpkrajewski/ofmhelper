"""The QuickTime atoms an iPhone writes that ffmpeg does not, built from a
`DeviceProfile` + `Recording`.

Custom on purpose: no proven tool writes these. ffmpeg's MOV muxer writes
`url ` data references, 0x7FFF languages and every `mdta` value as text;
exiftool writes Keys values but not handlers, data references, `tapt` or
track-level keys with a locale. The byte layouts are QuickTime File Format
(Apple, "Movie atoms" / "Metadata"); each builder is checked against the
real recordings in tests/iphones.
"""

import struct

from ofmhelpers.iphones.models import (
    DataType,
    DeviceProfile,
    Locale,
    MetadataKey,
    Recording,
)
from ofmhelpers.utils.mov_atoms import Atom

_FULL_BOX = bytes(4)  # version 0, flags 0
_KEY_NAMESPACE = b"mdta"
# mdta meta hdlr: pre_defined, handler "mdta", 3 reserved words, empty name.
_META_HDLR = _FULL_BOX + bytes(4) + b"mdta" + bytes(12) + bytes(2)
# dref entry flag: the media is in this same file.
_SELF_REFERENCE = 1


def pack_language(code: str) -> int:
    """ISO-639-2 code packed into 15 bits, as mdhd and locales store it."""
    a, b, c = (ord(ch) - 0x60 for ch in code)
    return (a << 10) | (b << 5) | c


def _locale(locale: Locale) -> bytes:
    country = locale.country.encode() if locale.country else bytes(2)
    language = pack_language(locale.language) if locale.language else 0
    return country + struct.pack(">H", language)


def _value(key: MetadataKey, recording: Recording) -> bytes | None:
    value = recording.value(key.per_recording) if key.per_recording else key.value
    if value is None:
        return None
    if key.data_type is DataType.SIGNED_INT_BE:
        return int(value).to_bytes(key.int_width or 4, "big", signed=True)
    return str(value).encode()


def meta(keys: list[MetadataKey], recording: Recording) -> Atom | None:
    """An `mdta` meta (hdlr, keys, ilst) holding `keys` in order; a key
    whose per-recording value is unset is left out, as an iPhone leaves out
    location with Location Services off."""
    present = [(k, v) for k in keys if (v := _value(k, recording)) is not None]
    if not present:
        return None
    names = b"".join(
        Atom(type=_KEY_NAMESPACE, payload=k.key.encode()).to_bytes() for k, _ in present
    )
    items = [
        Atom(
            type=struct.pack(">I", index),
            children=[
                Atom(
                    type=b"data",
                    payload=struct.pack(">I", k.data_type) + _locale(k.locale) + v,
                )
            ],
        )
        for index, (k, v) in enumerate(present, start=1)
    ]
    return Atom(
        type=b"meta",
        children=[
            Atom(type=b"hdlr", payload=_META_HDLR),
            Atom(
                type=b"keys",
                payload=_FULL_BOX + struct.pack(">I", len(present)) + names,
            ),
            Atom(type=b"ilst", children=items),
        ],
    )


def _pascal(text: str) -> bytes:
    raw = text.encode()
    return bytes([len(raw)]) + raw


def media_handler(profile: DeviceProfile, kind: str, name: str) -> Atom:
    """The mdia hdlr: component "mhlr", subtype `kind` (vide/soun)."""
    payload = (
        _FULL_BOX
        + b"mhlr"
        + kind.encode()
        + profile.handlers.vendor.encode()
        + bytes(8)
        + _pascal(name)
    )
    return Atom(type=b"hdlr", payload=payload)


def data_handler(profile: DeviceProfile) -> Atom:
    """The minf hdlr: component "dhlr", subtype = the data reference type."""
    h = profile.handlers
    payload = (
        _FULL_BOX
        + b"dhlr"
        + h.data_reference.encode()
        + h.vendor.encode()
        + bytes(8)
        + _pascal(h.data)
    )
    return Atom(type=b"hdlr", payload=payload)


def data_information(profile: DeviceProfile) -> Atom:
    """dinf > dref with one self-referencing entry of the profile's type."""
    entry = Atom(
        type=profile.handlers.data_reference.encode(),
        payload=struct.pack(">I", _SELF_REFERENCE),
    )
    dref = Atom(
        type=b"dref", payload=_FULL_BOX + struct.pack(">I", 1) + entry.to_bytes()
    )
    return Atom(type=b"dinf", payload=dref.to_bytes())


def video_media_header(profile: DeviceProfile) -> Atom:
    """vmhd: flags 1 (QuickTime "no lean ahead"), graphics mode, op color."""
    v = profile.video
    payload = struct.pack(">I", 1) + struct.pack(
        ">HHHH", v.graphics_mode, v.op_color, v.op_color, v.op_color
    )
    return Atom(type=b"vmhd", payload=payload)


def track_aperture(width: int, height: int) -> Atom:
    """tapt: clean, production and encoded-pixels apertures, all w x h."""
    dims = _FULL_BOX + struct.pack(">II", width << 16, height << 16)
    body = b"".join(
        Atom(type=kind, payload=dims).to_bytes() for kind in (b"clef", b"prof", b"enof")
    )
    return Atom(type=b"tapt", payload=body)
