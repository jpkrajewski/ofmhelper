import struct
lazy from pathlib import Path

from pydantic import BaseModel

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}
_HEADER = 8
_LARGE_HEADER = 16
_EPOCH_1904 = 2082844800


class Atom(BaseModel):
    type: bytes
    payload: bytes = b""
    children: list[Atom] | None = None

    def child(self, kind: bytes) -> Atom | None:
        return next((c for c in self.children or [] if c.type == kind), None)

    def all(self, kind: bytes) -> list[Atom]:
        return [c for c in self.children or [] if c.type == kind]

    def to_bytes(self) -> bytes:
        body = (
            b"".join(c.to_bytes() for c in self.children)
            if self.children is not None
            else self.payload
        )
        return struct.pack(">I4s", _HEADER + len(body), self.type) + body


def parse(data: bytes) -> list[Atom]:
    atoms: list[Atom] = []
    pos = 0
    while pos + _HEADER <= len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + _HEADER])
        header = _HEADER
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = _LARGE_HEADER
        elif size == 0:
            size = len(data) - pos
        body = data[pos + header : pos + size]
        if kind in CONTAINERS:
            atoms.append(Atom(type=kind, children=parse(body)))
        else:
            atoms.append(Atom(type=kind, payload=body))
        pos += size
    return atoms


class AtomSpan(BaseModel, frozen=True):
    kind: bytes
    offset: int
    size: int


class Moov(BaseModel):
    atom: Atom
    offset: int


def top_level(path: Path) -> list[AtomSpan]:
    found: list[AtomSpan] = []
    with path.open("rb") as f:
        offset = 0
        while header := f.read(_HEADER):
            if len(header) < _HEADER:
                break
            size, kind = struct.unpack(">I4s", header)
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = path.stat().st_size - offset
            found.append(AtomSpan(kind=kind, offset=offset, size=size))
            offset += size
            f.seek(offset)
    return found


def read_moov(path: Path) -> Moov:
    for span in top_level(path):
        if span.kind == b"moov":
            with path.open("rb") as f:
                f.seek(span.offset)
                [moov] = parse(f.read(span.size))
            return Moov(atom=moov, offset=span.offset)
    msg = f"{path.name} has no moov atom"
    raise ValueError(msg)


def set_times(atom: Atom, unix_seconds: int) -> None:
    if atom.payload[0] != 0:
        return
    stamp = struct.pack(">II", unix_seconds + _EPOCH_1904, unix_seconds + _EPOCH_1904)
    atom.payload = atom.payload[:4] + stamp + atom.payload[12:]


_DURATION_OFFSET = {b"mvhd": 16, b"tkhd": 20}


def duration(atom: Atom) -> int:
    at = _DURATION_OFFSET[atom.type]
    return int.from_bytes(atom.payload[at : at + 4], "big")


def set_duration(atom: Atom, value: int) -> None:
    at = _DURATION_OFFSET.get(atom.type, 8)
    atom.payload = atom.payload[:at] + value.to_bytes(4, "big") + atom.payload[at + 4 :]
