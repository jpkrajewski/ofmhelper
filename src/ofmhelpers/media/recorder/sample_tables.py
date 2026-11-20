import struct

from pydantic import BaseModel

from ofmhelpers.media.mov_atoms import Atom

_SYNC = b"sync"


class SampleTables(BaseModel):
    durations: list[int]
    offsets: list[int]
    sync_samples: list[int]

    @classmethod
    def from_stbl(cls, stbl: Atom) -> SampleTables:
        stts = stbl.child(b"stts")
        ctts = stbl.child(b"ctts")
        stss = stbl.child(b"stss")
        durations = _runs(stts.payload, signed=False) if stts else []
        offsets = (
            _runs(ctts.payload, signed=ctts.payload[0] == 1)
            if ctts
            else [0] * len(durations)
        )
        sync = _entries(stss.payload, ">I") if stss else []
        return cls(durations=durations, offsets=offsets, sync_samples=sync)


def _entries(payload: bytes, fmt: str) -> list[int]:
    count = struct.unpack(">I", payload[4:8])[0]
    size = struct.calcsize(fmt)
    body = payload[8 : 8 + count * size]
    if len(body) != count * size:
        msg = f"sample table cut short: {len(body)} of {count * size} bytes"
        raise struct.error(msg)
    return [value for chunk in struct.iter_unpack(fmt, body) for value in chunk]


def _runs(payload: bytes, *, signed: bool) -> list[int]:
    flat = _entries(payload, ">Ii" if signed else ">II")
    return [
        value
        for count, value in zip(flat[0::2], flat[1::2], strict=True)
        for _ in range(count)
    ]


def cslg(tables: SampleTables) -> Atom:
    decode = 0
    starts: list[int] = []
    ends: list[int] = []
    for duration, offset in zip(tables.durations, tables.offsets, strict=True):
        starts.append(decode + offset)
        ends.append(decode + offset + duration)
        decode += duration
    least, greatest = min(tables.offsets), max(tables.offsets)
    payload = bytes(4) + struct.pack(
        ">iiiii", max(0, -least), least, greatest, min(starts), max(ends)
    )
    return Atom(type=b"cslg", payload=payload)


def sync_group_description(nal_unit_type: int) -> Atom:
    payload = (
        bytes([1, 0, 0, 0]) + _SYNC + struct.pack(">II", 1, 1) + bytes([nal_unit_type])
    )
    return Atom(type=b"sgpd", payload=payload)


def sync_sample_to_group(tables: SampleTables) -> Atom:
    sync = set(tables.sync_samples)
    runs: list[list[int]] = []
    for number in range(1, len(tables.durations) + 1):
        group = 1 if number in sync else 0
        if runs and runs[-1][1] == group:
            runs[-1][0] += 1
        else:
            runs.append([1, group])
    body = b"".join(struct.pack(">II", count, group) for count, group in runs)
    return Atom(
        type=b"sbgp", payload=bytes(4) + _SYNC + struct.pack(">I", len(runs)) + body
    )


STBL_ORDER = (
    b"stsd", b"sgpd", b"sbgp", b"stts", b"ctts", b"cslg",
    b"stss", b"sdtp", b"stsc", b"stsz", b"co64", b"stco",
)  # fmt: skip


def ordered(children: list[Atom]) -> list[Atom]:
    rank = {kind: i for i, kind in enumerate(STBL_ORDER)}
    return sorted(children, key=lambda a: rank.get(a.type, len(rank)))
