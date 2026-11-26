# Copyright (C) 2026 jpkrajewski

import struct

from ofmhelpers.media.mov_atoms import Atom, parse
lazy from ofmhelpers.media.recorder.models import DeviceProfile

_STSD_HEADER = 8
_DATA_REFERENCE_INDEX = 1
_VIDEO_FIXED = 78
_VENDOR = slice(12, 16)
_QUALITY = slice(16, 24)
_KEPT_VIDEO_BOXES = (b"hvcC", b"colr")
_ES_DESCR, _DECODER_CONFIG, _DECODER_SPECIFIC, _SL_CONFIG = 3, 4, 5, 6
_OBJECT_TYPE_AAC = 0x40
_STREAM_TYPE_AUDIO = 0x05
_SL_PREDEFINED_MP4 = 2
_AAC_SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050)
_AAC_PROFILES = {"LC": 2}
_COMPRESSION_ID_VBR = -2
_AAC_FRAME_SAMPLES = 1024
_SAMPLE_SIZE_BITS = 16


def _descriptor(tag: int, body: bytes) -> bytes:
    n = len(body)
    length = bytes(
        [
            0x80 | (n >> 21 & 0x7F),
            0x80 | (n >> 14 & 0x7F),
            0x80 | (n >> 7 & 0x7F),
            n & 0x7F,
        ],
    )
    return bytes([tag]) + length + body


def audio_specific_config(profile: DeviceProfile) -> bytes:
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
        struct.pack(">HB", 0, 0)
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
        + struct.pack(">HH", 1, 0)
        + bytes(4)
        + struct.pack(">HHhH", a.channels, _SAMPLE_SIZE_BITS, _COMPRESSION_ID_VBR, 0)
        + struct.pack(">I", a.sample_rate << 16)
        + struct.pack(
            ">IIII",
            _AAC_FRAME_SAMPLES,
            1,
            a.channels,
            _SAMPLE_SIZE_BITS // 8,
        )
    )
    wave = Atom(
        type=b"wave",
        children=[
            Atom(type=b"frma", payload=tag),
            Atom(type=tag, payload=bytes(4)),
            esds(profile),
            Atom(type=bytes(4)),
        ],
    )
    return Atom(type=tag, payload=fixed + wave.to_bytes())


def video_entry(entry: Atom, profile: DeviceProfile) -> Atom:
    body = bytearray(entry.payload[:_VIDEO_FIXED])
    body[_VENDOR] = bytes(4)
    q = profile.video.sample_quality
    body[_QUALITY] = struct.pack(">II", q, q)
    boxes = {b.type: b for b in parse(entry.payload[_VIDEO_FIXED:])}
    kept = b"".join(boxes[t].to_bytes() for t in _KEPT_VIDEO_BOXES if t in boxes)
    return Atom(type=entry.type, payload=bytes(body) + kept)


def stsd(stsd_atom: Atom, kind: str, profile: DeviceProfile) -> Atom:
    header = stsd_atom.payload[:_STSD_HEADER]
    [entry] = parse(stsd_atom.payload[_STSD_HEADER:])
    if kind == "vide":
        entry = video_entry(entry, profile)
    elif kind == "soun":
        entry = audio_entry(profile)
    return Atom(type=b"stsd", payload=header + entry.to_bytes())
