"""The `stsd` sample descriptions as an iPhone writes them.

Layouts per QuickTime File Format ("Video sample description", "Sound
sample description (version 1)") and ISO 14496-1 (ES_Descriptor), checked
against the real recordings in tests/iphones:

- video: ffmpeg's `hvc1` entry keeps its `hvcC` and `colr`, in that order;
  ffmpeg's `fiel` / `pasp` / `btrt` (which an iPhone does not write) go,
  and the vendor and quality fields take the iPhone's values.
- audio: rebuilt whole from the profile: SoundDescription v1 + `wave`
  (`frma`, `mp4a`, `esds`, terminator) and no `chan`. Rebuilt rather than
  edited because ffmpeg's AAC encoder always appends 3 bytes of SBR sync
  extension to the AudioSpecificConfig, which no option turns off.
"""

import struct

from ofmhelpers.iphones.models import DeviceProfile
from ofmhelpers.utils.mov_atoms import Atom, parse

_STSD_HEADER = 8  # version/flags(4) entry_count(4)
_DATA_REFERENCE_INDEX = 1
# Video sample entry body: reserved(6) dref(2) version(2) revision(2)
# vendor(4) temporal(4) spatial(4) width(2) height(2) hres(4) vres(4)
# data_size(4) frame_count(2) compressorname(32) depth(2) color_table(2).
_VIDEO_FIXED = 78
_VENDOR = slice(12, 16)
_QUALITY = slice(16, 24)
_KEPT_VIDEO_BOXES = (b"hvcC", b"colr")
# MPEG-4 descriptor tags and fields.
_ES_DESCR, _DECODER_CONFIG, _DECODER_SPECIFIC, _SL_CONFIG = 3, 4, 5, 6
_OBJECT_TYPE_AAC = 0x40
_STREAM_TYPE_AUDIO = 0x05
_SL_PREDEFINED_MP4 = 2
_AAC_SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050)
_AAC_PROFILES = {"LC": 2}
# QuickTime sound description v1: compression id -2 (variable), and an AAC
# packet is one 1024-sample frame.
_COMPRESSION_ID_VBR = -2
_AAC_FRAME_SAMPLES = 1024
_SAMPLE_SIZE_BITS = 16


def _descriptor(tag: int, body: bytes) -> bytes:
    """An MPEG-4 descriptor with the 4-byte length form Apple writes."""
    n = len(body)
    length = bytes(
        [
            0x80 | (n >> 21 & 0x7F),
            0x80 | (n >> 14 & 0x7F),
            0x80 | (n >> 7 & 0x7F),
            n & 0x7F,
        ]
    )
    return bytes([tag]) + length + body


def audio_specific_config(profile: DeviceProfile) -> bytes:
    """AudioSpecificConfig: object type, sample-rate index, channels."""
    a = profile.audio
    value = (
        _AAC_PROFILES[a.profile] << 11
        | _AAC_SAMPLE_RATES.index(a.sample_rate) << 7
        | a.channels << 3
    )
    return value.to_bytes(2, "big")


def esds(profile: DeviceProfile) -> Atom:
    a = profile.audio
    decoder_config = (
        bytes([_OBJECT_TYPE_AAC, _STREAM_TYPE_AUDIO << 2])
        + a.buffer_size.to_bytes(3, "big")
        + struct.pack(">II", a.max_bitrate, a.avg_bitrate)
        + _descriptor(_DECODER_SPECIFIC, audio_specific_config(profile))
    )
    es = (
        struct.pack(">HB", 0, 0)  # ES_ID, flags
        + _descriptor(_DECODER_CONFIG, decoder_config)
        + _descriptor(_SL_CONFIG, bytes([_SL_PREDEFINED_MP4]))
    )
    return Atom(type=b"esds", payload=bytes(4) + _descriptor(_ES_DESCR, es))


def audio_entry(profile: DeviceProfile) -> Atom:
    a = profile.audio
    tag = a.codec_tag.encode()
    fixed = (
        bytes(6)
        + struct.pack(">H", _DATA_REFERENCE_INDEX)
        + struct.pack(">HH", 1, 0)  # version 1, revision
        + bytes(4)  # vendor
        + struct.pack(">HHhH", a.channels, _SAMPLE_SIZE_BITS, _COMPRESSION_ID_VBR, 0)
        + struct.pack(">I", a.sample_rate << 16)
        # v1: samples per packet, bytes per packet / frame / sample.
        + struct.pack(
            ">IIII", _AAC_FRAME_SAMPLES, 1, a.channels, _SAMPLE_SIZE_BITS // 8
        )
    )
    wave = Atom(
        type=b"wave",
        children=[
            Atom(type=b"frma", payload=tag),
            Atom(type=tag, payload=bytes(4)),
            esds(profile),
            Atom(type=bytes(4)),  # terminator
        ],
    )
    return Atom(type=tag, payload=fixed + wave.to_bytes())


def video_entry(entry: Atom, profile: DeviceProfile) -> Atom:
    """ffmpeg's video entry with only `hvcC` and `colr` kept, vendor zeroed
    and the quality fields set to the profile's."""
    body = bytearray(entry.payload[:_VIDEO_FIXED])
    body[_VENDOR] = bytes(4)
    q = profile.video.sample_quality
    body[_QUALITY] = struct.pack(">II", q, q)
    boxes = {b.type: b for b in parse(entry.payload[_VIDEO_FIXED:])}
    kept = b"".join(boxes[t].to_bytes() for t in _KEPT_VIDEO_BOXES if t in boxes)
    return Atom(type=entry.type, payload=bytes(body) + kept)


def stsd(stsd_atom: Atom, kind: str, profile: DeviceProfile) -> Atom:
    """The track's stsd with its one entry in the iPhone's form."""
    header = stsd_atom.payload[:_STSD_HEADER]
    [entry] = parse(stsd_atom.payload[_STSD_HEADER:])
    if kind == "vide":
        entry = video_entry(entry, profile)
    elif kind == "soun":
        entry = audio_entry(profile)
    return Atom(type=b"stsd", payload=header + entry.to_bytes())
