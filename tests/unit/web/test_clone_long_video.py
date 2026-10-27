"""web/clone_long/video.py: real ffmpeg -- cut one piece, join pieces,
keep the picture's shape (16:9 in, 16:9 out)."""

import json
import subprocess
from pathlib import Path

import pytest

from ofmhelpers.web.clone_long import Piece, Plan, Shape, VideoTools

pytestmark = pytest.mark.slow

TOOLS = VideoTools(timeout_s=60)


def _clip(
    dest: Path, seconds: float, *, size="64x36", audio=False, colour="red"
) -> Path:
    inputs = ["-f", "lavfi", "-i", f"color=c={colour}:size={size}:rate=10:d={seconds}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
    )
    return dest


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    streams = json.loads(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    return {
        "size": (video["width"], video["height"]),
        "audio": any(s["codec_type"] == "audio" for s in streams),
    }


def _rgb(path: Path, at: float = 0) -> tuple[int, int, int]:
    """The average colour of the frame at `at` seconds."""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    return raw[0], raw[1], raw[2]


def test_cut_gives_exactly_that_piece(tmp_path):
    src = _clip(tmp_path / "src.mp4", 20, audio=True)

    piece = TOOLS.cut(src, Piece(start=7, length=6.5), tmp_path / "p.mp4")

    assert TOOLS.length(piece) == pytest.approx(6.5, abs=0.15)
    assert _probe(piece)["audio"] is True
    assert not (tmp_path / "p.part.mp4").exists()


def test_a_plans_pieces_add_up_to_the_source(tmp_path):
    src = _clip(tmp_path / "src.mp4", 32)
    plan = Plan.split(32, 15)

    cuts = [
        TOOLS.cut(src, p, tmp_path / f"p{i}.mp4") for i, p in enumerate(plan.pieces)
    ]

    assert sum(TOOLS.length(c) for c in cuts) == pytest.approx(32, abs=0.3)


@pytest.mark.parametrize("size", ["1280x720", "720x1280", "640x640"])
def test_cut_and_join_keep_the_pictures_shape(tmp_path, size):
    """16:9 in, 16:9 out: nothing is scaled, cropped or padded."""
    src = _clip(tmp_path / "src.mp4", 4, size=size)
    parts = [
        TOOLS.cut(src, Piece(start=0, length=2), tmp_path / "a.mp4"),
        TOOLS.cut(src, Piece(start=2, length=2), tmp_path / "b.mp4"),
    ]

    width, height = (int(x) for x in size.split("x"))
    out = TOOLS.join(
        parts,
        tmp_path / "out.mp4",
        Shape(width=width, height=height),
        work=tmp_path / "work",
    )

    assert _probe(out)["size"] == (width, height)
    assert TOOLS.shape(out) == Shape(width=width, height=height)


@pytest.mark.parametrize("audio", [False, True])
def test_join_plays_the_parts_one_after_another_silent(tmp_path, audio):
    parts = [
        _clip(tmp_path / f"g{i}.mp4", 2, audio=audio, colour=c)
        for i, c in enumerate(["red", "green", "blue"])
    ]

    out = TOOLS.join(
        parts,
        tmp_path / "out" / "joined.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "work",
    )

    assert TOOLS.length(out) == pytest.approx(6, abs=0.2)
    assert _probe(out)["audio"] is False  # a long clone has no sound
    assert [p.name for p in out.parent.iterdir()] == ["joined.mp4"]


@pytest.mark.parametrize("colour_match", [False, True])
def test_join_keeps_the_pieces_sound_when_asked(tmp_path, colour_match):
    parts = [_clip(tmp_path / f"g{i}.mp4", 2, audio=True) for i in range(2)]

    out = TOOLS.join(
        parts,
        tmp_path / "out" / "joined.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "work",
        colour_match=colour_match,
        audio=True,
    )

    assert _probe(out)["audio"] is True
    assert TOOLS.length(out) == pytest.approx(4, abs=0.2)


def test_colour_match_pulls_every_piece_to_the_first_pieces_colour(tmp_path):
    """The second piece came out tinted: joined, it plays in the first
    piece's colour; the matched copies are not left behind."""
    parts = [
        _clip(tmp_path / "first.mp4", 2, colour="0x808080"),
        _clip(tmp_path / "second.mp4", 2, colour="0x8c7464"),
    ]

    out = TOOLS.join(
        parts,
        tmp_path / "out" / "joined.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "work",
        colour_match=True,
    )

    first, second = _rgb(out, 0.5), _rgb(out, 3)
    assert all(abs(a - b) <= 6 for a, b in zip(first, second, strict=True))
    assert [p.name for p in out.parent.iterdir()] == ["joined.mp4"]
    assert list((tmp_path / "work").iterdir()) == []


def test_colour_match_never_pushes_a_dark_piece_to_white(tmp_path):
    """A piece that is mostly dark (a fade, a night shot) is pulled at most
    x1.33 per channel, not by the 12x its average asks for."""
    parts = [
        _clip(tmp_path / "first.mp4", 2, colour="0x787878"),
        _clip(tmp_path / "dark.mp4", 2, colour="0x0a0a0a"),
    ]

    out = TOOLS.join(
        parts,
        tmp_path / "joined.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "work",
        colour_match=True,
    )

    assert max(_rgb(out, 3)) < 30


def test_colour_match_writes_its_copies_only_in_the_runs_work_folder(
    tmp_path, monkeypatch
):
    """The output folder is every job's: two joins at once must never
    share a copy's name there."""
    written = []
    real_write = VideoTools._write

    def write(self, args, dest):
        written.append(dest)
        return real_write(self, args, dest)

    monkeypatch.setattr(VideoTools, "_write", write)
    parts = [
        _clip(tmp_path / f"p{i}.mp4", 1, colour=c)
        for i, c in enumerate(["red", "green", "blue"])
    ]
    out_dir = tmp_path / "shared-out"

    out = TOOLS.join(
        parts,
        out_dir / "joined.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "job1",
        colour_match=True,
    )

    assert [d for d in written if d.parent != tmp_path / "job1"] == [out]
    assert len(written) == 3


def test_a_colour_match_failing_part_way_leaves_nothing_behind(tmp_path):
    """The third piece cannot be read: the copy already made of the second
    is removed, and the failure names ffmpeg, not its command line."""
    good = _clip(tmp_path / "good.mp4", 1)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    with pytest.raises(RuntimeError, match="ffmpeg failed") as caught:
        TOOLS.join(
            [good, good, broken],
            tmp_path / "o" / "out.mp4",
            Shape(width=16, height=9),
            work=tmp_path / "work",
            colour_match=True,
        )

    assert "rawvideo" not in str(caught.value)
    assert list((tmp_path / "work").iterdir()) == []
    assert not (tmp_path / "o").exists()


def test_without_colour_match_each_piece_keeps_its_own_colour(tmp_path):
    parts = [
        _clip(tmp_path / "first.mp4", 2, colour="0x808080"),
        _clip(tmp_path / "second.mp4", 2, colour="0x8c7464"),
    ]

    out = TOOLS.join(
        parts, tmp_path / "joined.mp4", Shape(width=16, height=9), work=tmp_path / "w"
    )

    assert _rgb(out, 3)[2] < _rgb(out, 0.5)[2] - 20


def test_last_frame_is_the_videos_very_last_frame(tmp_path):
    """The next chained piece starts from it: only the last 0.1 s (one
    frame at 10 fps) is blue, and that is the frame taken."""
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                "color=c=red:size=64x36:rate=10:d=2,"
                "drawbox=color=blue:t=fill:enable='gte(t,1.9)'"
            ),
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )

    frame = TOOLS.last_frame(video, tmp_path / "chain" / "last.png")

    assert frame == tmp_path / "chain" / "last.png"
    red, _, blue = _rgb(frame)
    assert blue > 200
    assert red < 50


def test_join_takes_names_with_quotes_and_spaces(tmp_path):
    parts = [_clip(tmp_path / f"it's part {i}.mp4", 1) for i in range(2)]
    out = TOOLS.join(
        parts, tmp_path / "o.mp4", Shape(width=16, height=9), work=tmp_path / "w"
    )
    assert TOOLS.length(out) == pytest.approx(2, abs=0.2)


def test_a_failed_join_leaves_nothing_behind(tmp_path):
    """ffmpeg dies mid-join (here: the second part is gone): no output, no
    half-written temp file, no listing."""
    first = _clip(tmp_path / "first.mp4", 1)

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        TOOLS.join(
            [first, tmp_path / "missing.mp4"],
            tmp_path / "o" / "out.mp4",
            Shape(width=64, height=36),
            work=tmp_path / "work",
        )
    assert list((tmp_path / "o").iterdir()) == []
    assert list((tmp_path / "work").iterdir()) == []


def test_join_fits_pieces_of_a_near_ratio_into_the_sources_exact_shape(tmp_path):
    """A 4:5 source: Wan makes 3:4 (its nearest), the joined video is 4:5
    again -- scaled to fit, never cropped."""
    parts = [_clip(tmp_path / f"p{i}.mp4", 1, size="768x1024") for i in range(2)]

    out = TOOLS.join(
        parts,
        tmp_path / "out.mp4",
        Shape(width=1080, height=1350),
        work=tmp_path / "work",
    )

    assert _probe(out)["size"] == (818, 1024)


def test_shape_reads_a_sideways_phone_video_as_it_plays(tmp_path):
    flat = _clip(tmp_path / "flat.mp4", 1, size="64x36")
    turned = tmp_path / "turned.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(flat),
            "-c",
            "copy",
            str(turned),
        ],
        check=True,
    )
    assert TOOLS.shape(flat) == Shape(width=64, height=36)
    assert TOOLS.shape(turned) == Shape(width=36, height=64)


def test_cut_scales_a_piece_to_the_size_given(tmp_path):
    """A 4K source's pieces go up at most 1080 on the short side (here in
    small): the piece is that size, the same length."""
    src = _clip(tmp_path / "src.mp4", 4, size="128x72")

    piece = TOOLS.cut(src, Piece(start=1, length=2), tmp_path / "p.mp4", (64, 36))

    assert _probe(piece)["size"] == (64, 36)
    assert TOOLS.length(piece) == pytest.approx(2, abs=0.15)


def test_join_reads_many_pieces_of_any_size_into_one_shape(tmp_path):
    """Pieces are read one after another (never all open at once), each
    fitted to the one frame; the listing it reads them from is not left."""
    parts = [
        _clip(tmp_path / f"p{i}.mp4", 1, size=size, colour=colour)
        for i, (size, colour) in enumerate(
            [("64x36", "red"), ("128x72", "green"), ("48x64", "blue")] * 4
        )
    ]

    out = TOOLS.join(
        parts,
        tmp_path / "o" / "out.mp4",
        Shape(width=16, height=9),
        work=tmp_path / "work",
    )

    assert _probe(out)["size"] == (64, 36)
    assert TOOLS.length(out) == pytest.approx(12, abs=0.3)
    assert [p.name for p in out.parent.iterdir()] == ["out.mp4"]
    assert list((tmp_path / "work").iterdir()) == []


def test_a_broken_piece_fails_the_join_instead_of_ending_it_early(tmp_path):
    """ffmpeg's concat input stops quietly at a piece it cannot read (exit
    0, a short video): the join must fail instead."""
    first = _clip(tmp_path / "first.mp4", 2)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        TOOLS.join(
            [first, broken, first],
            tmp_path / "o" / "out.mp4",
            Shape(width=16, height=9),
            work=tmp_path / "work",
        )
    assert list((tmp_path / "o").iterdir()) == []
