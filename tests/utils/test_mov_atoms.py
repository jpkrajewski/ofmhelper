import struct

from ofmhelpers.utils.mov_atoms import Atom, parse, set_times


def _atom(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def test_round_trip_is_byte_identical():
    mdhd = _atom(b"mdhd", bytes(24))
    trak = _atom(b"trak", _atom(b"mdia", mdhd) + _atom(b"tkhd", b"x" * 84))
    moov = _atom(b"moov", _atom(b"mvhd", bytes(100)) + trak)

    [atom] = parse(moov)

    assert atom.to_bytes() == moov
    assert atom.all(b"trak")[0].child(b"mdia").child(b"mdhd").payload == bytes(24)


def test_resizing_a_child_resizes_its_parents():
    [moov] = parse(_atom(b"moov", _atom(b"trak", _atom(b"tkhd", b"ab"))))

    moov.children[0].children.append(Atom(type=b"tapt", payload=b"1234"))

    assert moov.to_bytes() == _atom(
        b"moov", _atom(b"trak", _atom(b"tkhd", b"ab") + _atom(b"tapt", b"1234"))
    )


def test_set_times_writes_both_1904_based_stamps():
    atom = Atom(type=b"mvhd", payload=bytes(100))

    set_times(atom, 0)

    assert atom.payload[4:12] == struct.pack(">II", 2082844800, 2082844800)
    assert atom.payload[12:] == bytes(88)
